// In-memory cache + lazy loaders for the sidebar tree. Invalidated by api.ts after mutations.
import { useSyncExternalStore } from "react";
import { get } from "@rdb/core";
import type { Client, Project, ViewSummary } from "./api";

export interface Entry<T> { data: T[] | null; loading: boolean; stale: boolean; error: string | null }
const blank = <T,>(): Entry<T> => ({ data: null, loading: false, stale: false, error: null });

const clients: Entry<Client> = blank();
const projects = new Map<string, Entry<Project>>();
const views = new Map<string, Entry<ViewSummary>>();

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

export function ensureClients(retry = false) { void load(clients, "/clients", () => {}, retry); }
export function ensureProjects(c: string, retry = false) {
  const e = projects.get(c) ?? blank<Project>();
  void load(e, `/clients/${c}/projects`, (x) => projects.set(c, x), retry);
}
export function ensureViews(c: string, p: string, retry = false) {
  const e = views.get(`${c}/${p}`) ?? blank<ViewSummary>();
  void load(e, `/clients/${c}/projects/${p}/views`, (x) => views.set(`${c}/${p}`, x), retry);
}

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

/** Called by api.ts after every successful non-GET request. Path is relative to /api. */
export function invalidateForMutation(method: string, path: string) {
  if (method === "GET") return;
  const s = path.split("?")[0].split("/").filter(Boolean);
  if (s[0] !== "clients") return;
  if (s.length === 1) { invalidateClients(); return; }
  const c = s[1];
  if (s[2] !== "projects") return;
  if (s.length === 3) { invalidateProjects(c); return; } // create project
  const p = s[3];
  if (s.length === 4 && method === "PATCH") { invalidateProjects(c); dropViews(c, p); return; } // rename
  if (s[4] === "views") invalidateViews(c, p); // create / rename / save / publish / restore
}
