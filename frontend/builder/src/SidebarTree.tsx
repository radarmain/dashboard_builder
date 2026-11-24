import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Box, CircularProgress, Collapse, IconButton, List, ListItemButton, TextField, Typography } from "@mui/material";
import { Icon } from "@rdb/core";
import { Btn } from "./ui";
import {
  clientsEntry, ensureClients, ensureFavorites, ensureProjects, ensureViews, favoritesEntry, projectsEntry, shown, useNavCache, viewsEntry, type Entry,
} from "./navTree";
import { FavStar, ShowArchivedToggle, type ItemRef } from "./Lifecycle";
import type { Favorite } from "./api";

type Status = "draft" | "published" | "changes";
interface TNode {
  id: string; kind: "client" | "project" | "view"; label: string; slug: string; to: string; level: number;
  status?: Status; archived?: boolean; parent?: string; kids: TNode[]; entry?: Entry<unknown>; retry?: () => void; leaf: boolean;
}

const STORE_KEY = "rdb.sidebar.expanded";
function readExpanded(): Set<string> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(STORE_KEY) ?? "[]");
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  } catch { return new Set(); }
}
function writeExpanded(s: Set<string>) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify([...s])); } catch { /* storage unavailable: work without it */ }
}

const STATUS_LABEL: Record<Status, string> = { draft: "draft", published: "published", changes: "unpublished changes" };
const textSx = { fontWeight: 500, fontSize: "0.8125rem", lineHeight: "18px" };

/** Builds the nodes that are loaded so far; `q` filters by name/slug across them (client-side only). */
function buildTree(q: string): TNode[] {
  const hit = (n: { label: string; slug: string }) => !q || n.label.toLowerCase().includes(q) || n.slug.toLowerCase().includes(q);
  const out: TNode[] = [];
  for (const c of (clientsEntry().data ?? []).filter(shown)) {
    const ce = projectsEntry(c.slug);
    const cn: TNode = {
      id: `c:${c.slug}`, kind: "client", label: c.name || c.slug, slug: c.slug, to: `/c/${c.slug}`, level: 1, kids: [],
      entry: ce as Entry<unknown>, retry: () => ensureProjects(c.slug, true), leaf: false,
      archived: c.effective_lifecycle === "archived",
    };
    const cSelf = hit(cn);
    for (const p of (ce.data ?? []).filter(shown)) {
      const ve = viewsEntry(c.slug, p.slug);
      const pn: TNode = {
        id: `p:${c.slug}/${p.slug}`, kind: "project", label: p.name || p.slug, slug: p.slug, to: `/c/${c.slug}/p/${p.slug}`, level: 2,
        parent: cn.id, kids: [], entry: ve as Entry<unknown>, retry: () => ensureViews(c.slug, p.slug, true), leaf: false,
        archived: p.effective_lifecycle === "archived",
      };
      const pSelf = hit(pn);
      for (const v of (ve.data ?? []).filter(shown)) {
        const vn: TNode = {
          id: `v:${c.slug}/${p.slug}/${v.slug}`, kind: "view", label: v.name || v.slug, slug: v.slug,
          to: `/c/${c.slug}/p/${p.slug}/v/${v.slug}`, level: 3, parent: pn.id, kids: [], leaf: true, archived: v.effective_lifecycle === "archived",
          status: v.published_revision === null ? "draft" : v.has_unpublished_changes ? "changes" : "published",
        };
        if (!q || cSelf || pSelf || hit(vn)) pn.kids.push(vn);
      }
      if (!q || cSelf || pSelf || pn.kids.length) cn.kids.push(pn);
    }
    if (!q || cSelf || cn.kids.length) out.push(cn);
  }
  return out;
}

const favTo = (f: Favorite) =>
  f.type === "client" ? `/c/${f.client}` : f.type === "project" ? `/c/${f.client}/p/${f.project}` : `/c/${f.client}/p/${f.project}/v/${f.view}`;

function FavoritesSection({ favs, pathname }: { favs: Favorite[]; pathname: string }) {
  const [open, setOpen] = useState(true);
  return (
    <Box component="section" aria-label="Favorites" sx={{ flex: "none", maxHeight: "30%", overflow: "auto", mb: 0.5 }}>
      <ListItemButton dense onClick={() => setOpen((o) => !o)} aria-expanded={open} sx={{ gap: 1 }}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={12} />
        <Typography sx={textSx}>Favorites</Typography>
      </ListItemButton>
      <Collapse in={open} unmountOnExit>
        <List disablePadding dense>
          {favs.map((f) => (
            <ListItemButton key={`${f.type}/${f.client}/${f.project ?? ""}/${f.view ?? ""}`} component={Link} to={favTo(f)} dense selected={pathname === favTo(f)} sx={{ gap: 1, pl: 2 }}>
              <Icon name={f.type === "client" ? "client" : f.type === "project" ? "project" : "view"} size={13} />
              <Box sx={{ minWidth: 0 }}>
                <Typography noWrap title={f.name} sx={textSx}>{f.name}</Typography>
                {f.path && <Typography noWrap variant="caption" color="text.secondary" sx={{ display: "block", lineHeight: "14px" }}>{f.path}</Typography>}
              </Box>
            </ListItemButton>
          ))}
        </List>
      </Collapse>
    </Box>
  );
}

export function SidebarTree() {
  const { pathname } = useLocation();
  const nav = useNavigate();
  const [, , curC, , curP, , curV] = pathname.split("/"); // /c/:client/p/:project/v/:view
  const ver = useNavCache();
  const [expanded, setExpanded] = useState<Set<string>>(readExpanded);
  const [filter, setFilter] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const els = useRef(new Map<string, HTMLElement>());
  const q = filter.trim().toLowerCase();

  const update = (fn: (s: Set<string>) => void) =>
    setExpanded((prev) => { const s = new Set(prev); fn(s); writeExpanded(s); return s; });

  // Auto-expand the path to the current page (only when the route changes, so manual collapse sticks).
  useEffect(() => {
    if (!pathname.startsWith("/c/") || !curC) return;
    update((s) => { s.add(`c:${curC}`); if (curP) s.add(`p:${curC}/${curP}`); });
  }, [curC, curP]); // eslint-disable-line react-hooks/exhaustive-deps

  // Lazy loading: clients now, children when a node is expanded (also refetches stale entries).
  useEffect(() => {
    ensureClients();
    ensureFavorites();
    for (const k of expanded) {
      if (k.startsWith("c:")) ensureProjects(k.slice(2));
      else if (k.startsWith("p:")) { const [c, p] = k.slice(2).split("/"); ensureViews(c, p); }
    }
  }, [expanded, ver]);

  const tree = useMemo(() => buildTree(q), [q, ver]); // eslint-disable-line react-hooks/exhaustive-deps
  const isOpen = (n: TNode) => !n.leaf && (expanded.has(n.id) || (!!q && n.kids.length > 0));

  const flat: TNode[] = [];
  const byId = new Map<string, TNode>();
  const walk = (n: TNode) => { flat.push(n); byId.set(n.id, n); if (isOpen(n)) n.kids.forEach(walk); };
  tree.forEach(walk);

  const selectedId = curV ? `v:${curC}/${curP}/${curV}` : curP ? `p:${curC}/${curP}` : curC ? `c:${curC}` : null;
  const onPath = (n: TNode) => n.id !== selectedId && isAncestor(n, curC, curP);
  const tabId = focusId && byId.has(focusId) ? focusId : selectedId && byId.has(selectedId) ? selectedId : flat[0]?.id;

  const focusNode = (id: string | undefined) => { if (!id) return; setFocusId(id); els.current.get(id)?.focus(); };
  const toggle = (n: TNode, open?: boolean) =>
    update((s) => { if (open ?? !expanded.has(n.id)) s.add(n.id); else s.delete(n.id); });

  function onKey(e: React.KeyboardEvent, n: TNode) {
    if (e.target !== e.currentTarget || e.altKey || e.ctrlKey || e.metaKey) return;
    const i = flat.indexOf(n);
    const open = isOpen(n);
    const act = (fn: () => void) => { e.preventDefault(); fn(); };
    switch (e.key) {
      case "ArrowDown": act(() => focusNode(flat[i + 1]?.id)); break;
      case "ArrowUp": act(() => focusNode(flat[i - 1]?.id)); break;
      case "Home": act(() => focusNode(flat[0]?.id)); break;
      case "End": act(() => focusNode(flat[flat.length - 1]?.id)); break;
      case "ArrowRight": act(() => { if (n.leaf) return; if (!open) toggle(n, true); else focusNode(n.kids[0]?.id); }); break;
      case "ArrowLeft": act(() => { if (open && !q) toggle(n, false); else focusNode(n.parent); }); break;
      case " ": act(() => { if (!n.leaf) toggle(n); }); break;
      case "Enter": act(() => nav(n.to)); break;
    }
  }

  function renderNode(n: TNode) {
    const open = isOpen(n);
    const selected = n.id === selectedId;
    const loading = !n.leaf && !!n.entry?.loading && open;
    return (
      <Box component="li" role="none" key={n.id}>
        <Box
          role="treeitem" tabIndex={tabId === n.id ? 0 : -1} aria-level={n.level} aria-selected={selected}
          aria-expanded={n.leaf ? undefined : open}
          aria-label={`${n.label}${n.status ? `, ${STATUS_LABEL[n.status]}` : ""}${n.archived ? ", archived" : ""}`}
          ref={(el: HTMLElement | null) => { if (el) els.current.set(n.id, el); else els.current.delete(n.id); }}
          onKeyDown={(e) => onKey(e, n)} onFocus={(e) => { if (e.target === e.currentTarget) setFocusId(n.id); }}
          sx={(t) => ({ display: "flex", alignItems: "center", pl: `${(n.level - 1) * 12}px`, opacity: n.archived ? 0.6 : 1, outline: "none", "&:focus-visible > .MuiListItemButton-root": { outline: `2px solid ${t.palette.secondary.main}`, outlineOffset: -2 }, "&:hover .rdb-star, &:focus-within .rdb-star": { opacity: 1 } })}
        >
          <Box sx={{ width: 24, height: 24, flexShrink: 0, display: "grid", placeItems: "center" }}>
            {loading ? <CircularProgress size={12} aria-label="Loading" /> : !n.leaf && (
              <IconButton size="small" tabIndex={-1} aria-hidden onClick={() => toggle(n)} sx={{ p: 0.25 }}>
                <Icon name={open ? "chevron-down" : "chevron-right"} size={12} />
              </IconButton>
            )}
          </Box>
          <ListItemButton
            component={Link} to={n.to} tabIndex={-1} selected={selected} dense
            onMouseDown={(e: React.MouseEvent) => e.preventDefault()}
            onClick={() => { setFocusId(n.id); els.current.get(n.id)?.focus(); }}
            sx={{ flex: 1, minWidth: 0, py: 0.5, pl: 1, gap: 1, borderRadius: "999px" }}
          >
            {n.status && (
              <Box component="span" aria-hidden sx={(t) => ({
                width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                bgcolor: { draft: t.palette.tokens.outline, changes: t.palette.tokens.warning, published: t.palette.tokens.success }[n.status!],
              })} />
            )}
            <Icon name={n.archived ? "archive" : n.kind === "client" ? "client" : n.kind === "project" ? (open ? "project-open" : "project") : "view"} size={13} />
            <Typography noWrap title={n.label} sx={{ ...textSx, fontWeight: selected || onPath(n) ? 600 : 500, fontStyle: n.archived ? "italic" : "normal" }}>{n.label}</Typography>
          </ListItemButton>
          <FavStar item={itemOf(n)} name={n.label} path={pathOf(n)} />
        </Box>
        {!n.leaf && (
          <Collapse in={open} timeout="auto" unmountOnExit>
            <List role="group" disablePadding dense>
              {n.kids.map(renderNode)}
              {!n.entry?.loading && n.entry?.error && (
                <Box component="li" role="none" sx={{ pl: `${n.level * 12 + 24}px` }}>
                  <Typography variant="caption" color="error">Couldn't load. </Typography>
                  <Btn variant="ghost" size="small" onClick={n.retry} sx={{ minHeight: 20, p: 0.5 }}>Retry</Btn>
                </Box>
              )}
              {!q && n.entry?.data && n.entry.data.length === 0 && (
                <Box component="li" role="none" sx={{ pl: `${n.level * 12 + 24}px`, py: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">{n.kind === "client" ? "No projects" : "No views"}</Typography>
                </Box>
              )}
            </List>
          </Collapse>
        )}
      </Box>
    );
  }

  const ce = clientsEntry();
  const favs = (favoritesEntry().data ?? []).filter((f) => !q || f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q));
  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Box sx={{ px: 0.5, pb: 1 }}>
        <TextField
          size="small" placeholder="Filter loaded items" value={filter} onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setFilter(""); }}
          slotProps={{ htmlInput: { "aria-label": "Filter the sidebar tree by name" } }}
        />
      </Box>
      <ShowArchivedToggle />
      {favs.length > 0 && <FavoritesSection favs={favs} pathname={pathname} />}
      <ListItemButton component={Link} to="/" selected={pathname === "/"} dense sx={{ flex: "none" }}>
        <Typography sx={textSx}>Clients</Typography>
      </ListItemButton>
      <Box sx={{ flex: 1, overflow: "auto", mt: 0.25 }}>
        <List role="tree" aria-label="Clients, projects and views" disablePadding dense sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
          {tree.map(renderNode)}
        </List>
        {ce.loading && !ce.data && <Box sx={{ px: 2, py: 1 }}><CircularProgress size={14} aria-label="Loading clients" /></Box>}
        {ce.error && (
          <Box sx={{ px: 2 }}>
            <Typography variant="caption" color="error">Couldn't load clients. </Typography>
            <Btn variant="ghost" size="small" onClick={() => ensureClients(true)} sx={{ minHeight: 20, p: 0.5 }}>Retry</Btn>
          </Box>
        )}
        {q && ce.data && tree.length === 0 && <Typography variant="caption" color="text.secondary" sx={{ px: 2 }}>No loaded items match.</Typography>}
      </Box>
    </Box>
  );
}

/** Ids are `c:<client>`, `p:<client>/<project>`, `v:<client>/<project>/<view>`. */
function itemOf(n: TNode): ItemRef {
  const [c, p, v] = n.id.slice(2).split("/");
  return { kind: n.kind, client: c, project: p, view: v };
}
const pathOf = (n: TNode) => { const [c, p] = n.id.slice(2).split("/"); return n.kind === "client" ? "" : n.kind === "project" ? c : `${c} / ${p}`; };

function isAncestor(n: TNode, c?: string, p?: string): boolean {
  return (n.kind === "client" && n.slug === c) || (n.kind === "project" && n.id === `p:${c}/${p}`);
}
