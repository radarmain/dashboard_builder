// In-memory cache + lazy loaders for the sidebar tree, favorites and the "Show archived" flag.
// Invalidated by api.ts after mutations. Lists are always fetched WITH archived items and filtered
// here, so toggling "Show archived" needs no refetch.
import { useSyncExternalStore } from "react";
import { api, get } from "@rdb/core";
import type { Client, FavRef, Favorite, Project, ViewSummary } from "./api";

export interface Entry<T> { data: T[] | null; loading: boolean; stale: boolean; error: string | null }
const blank = <T,>(): Entry<T> => ({ data: null, loading: false, stale: false, error: null });

const clients: Entry<Client> = blank();
const projects = new Map<string, Entry<Project>>();
const views = new Map<string, Entry<ViewSummary>>();
const favs: Entry<Favorite> = blank();

let version = 0;
const subs = new Set<() => void>();
const emit = () => { version++; subs.forEach((f) => f()); };

/** Re-renders the caller whenever any cache entry changes. */
export function useNavCache(): number {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => version);
}

export const clientsEntry = () => clients;
export const projectsEntry = (c: string): Entry<Project> => projects.get(c) ?? blank();
export const viewsEntry = (c: string, p: string): Entry<ViewSummary> => views.get(`${c}/${p}`) ?? blank();
export const favoritesEntry = () => favs;

// ---- "Show archived" (default off, remembered; storage may be unavailable) ----
const ARCHIVED_KEY = "rdb.showArchived";
let showArchived = (() => { try { return localStorage.getItem(ARCHIVED_KEY) === "1"; } catch { return false; } })();
export const getShowArchived = () => showArchived;
export function setShowArchived(v: boolean) {
  showArchived = v;
  try { localStorage.setItem(ARCHIVED_KEY, v ? "1" : "0"); } catch { /* work without it */ }
  emit();
}
/** Whether a list should show this item (archived ones only when the toggle is on). */
export const shown = (item: { effective_lifecycle?: string; lifecycle: string }) =>
  showArchived || (item.effective_lifecycle ?? item.lifecycle) !== "archived";

async function load<T>(entry: Entry<T>, path: string, store: (e: Entry<T>) => void, retry: boolean) {
  if (entry.loading || (entry.data && !entry.stale) || (entry.error && !retry)) return;
  entry.loading = true;
  entry.error = null;
  store(entry);
  emit();
  try {
    const data = await get<T[]>(path);
    entry.data = data;
    entry.stale = false;
  } catch (e) {
    entry.error = e instanceof Error ? e.message : String(e);
    entry.stale = false; // no automatic retry loop: only invalidate or the Retry button retries
  } finally {
    entry.loading = false;
    emit();
  }
}

const AR = "include_archived=true";
export function ensureClients(retry = false) { void load(clients, `/clients?${AR}`, () => {}, retry); }
export function ensureProjects(c: string, retry = false) {
  const e = projects.get(c) ?? blank<Project>();
  void load(e, `/clients/${c}/projects?${AR}`, (x) => projects.set(c, x), retry);
}
export function ensureViews(c: string, p: string, retry = false) {
  const e = views.get(`${c}/${p}`) ?? blank<ViewSummary>();
  void load(e, `/clients/${c}/projects/${p}/views?${AR}`, (x) => views.set(`${c}/${p}`, x), retry);
}
export function ensureFavorites(retry = false) { void load(favs, "/me/favorites", () => {}, retry); }

/** Marks stale (data stays visible until the refetch lands) and clears errors. */
function stale<T>(e: Entry<T> | undefined) {
  if (!e) return;
  e.stale = true;
  e.error = null;
}
export function invalidateClients() { stale(clients); emit(); }
export function invalidateProjects(c: string) { stale(projects.get(c)); emit(); }
export function invalidateViews(c: string, p: string) { stale(views.get(`${c}/${p}`)); emit(); }
export function dropViews(c: string, p: string) { views.delete(`${c}/${p}`); emit(); }
export function invalidateFavorites() { stale(favs); emit(); }
/** After archive / restore / delete: anything may have changed visibility. */
export function invalidateAll() {
  stale(clients); stale(favs);
  projects.forEach(stale); views.forEach(stale);
  emit();
}

// ---- favorites ----
export const favKey = (r: FavRef) => [r.type, r.client, r.project ?? "", r.view ?? ""].join("/");
export const isFavorite = (r: FavRef) => !!favs.data?.some((f) => favKey(f) === favKey(r));

/** Optimistic add/remove; reloads on failure and rethrows so the caller can show the message. */
export async function setFavorite(r: FavRef, on: boolean, meta?: { name: string; path: string }) {
  const before = favs.data;
  if (on && !isFavorite(r)) {
    favs.data = [...(favs.data ?? []), { ...r, name: meta?.name ?? r.view ?? r.project ?? r.client, path: meta?.path ?? "", status: null, created_at: "" }];
  } else if (!on) favs.data = (favs.data ?? []).filter((f) => favKey(f) !== favKey(r));
  emit();
  try {
    await api(on ? "PUT" : "DELETE", "/me/favorites", r);
    stale(favs);
    emit();
  } catch (e) {
    favs.data = before;
    emit();
    throw e;
  }
}

/** Called by api.ts after every successful non-GET request. Path is relative to /api. */
export function invalidateForMutation(method: string, path: string) {
  if (method === "GET") return;
  const s = path.split("?")[0].split("/").filter(Boolean);
  if (s[0] !== "clients") return;
  const last = s[s.length - 1];
  const lifecycleCall = (last === "archive" || last === "restore") || (method === "DELETE" && [2, 4, 6].includes(s.length) && (s.length === 2 || s[2] === "projects"));
  if (lifecycleCall) { invalidateAll(); return; }
  if (s.length === 1) { invalidateClients(); return; }
  const c = s[1];
  if (s[2] !== "projects") return;
  if (s.length === 3) { invalidateProjects(c); return; } // create project
  const p = s[3];
  if (s.length === 4 && method === "PATCH") { invalidateProjects(c); dropViews(c, p); invalidateFavorites(); return; } // rename
  if (s[4] === "views") { invalidateViews(c, p); if (method === "PATCH") invalidateFavorites(); } // create / rename / save / publish / restore
}
