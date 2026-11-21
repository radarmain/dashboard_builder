import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link as RouterLink, Outlet } from "react-router-dom";
import { AppBar, Avatar, Box, Breadcrumbs, IconButton, Link, Menu, MenuItem, Toolbar, Typography } from "@mui/material";
import { LOGO, useAuth } from "@rdb/core";
import { usePortal, usePortalParams } from "./context";
import { clientNameOf } from "./api";

/** Height of the Builder's preview header; the portal sits below it and its app bar sticks under it. */
export const PREVIEW_BAR_HEIGHT = 32;

interface ShellCtx { setClientName: (slug: string, name: string) => void }
const Ctx = createContext<ShellCtx>({ setClientName: () => {} });

/** Pages call this once their data arrives so the app bar can show the client name. */
export function useReportClient(slug: string, name: string | undefined) {
  const { setClientName } = useContext(Ctx);
  useEffect(() => {
    if (name) setClientName(slug, name);
  }, [slug, name, setClientName]);
}

/** Sets "<title> · RADaR" style document titles and restores the previous one on unmount. */
export function useDocTitle(...parts: (string | undefined)[]) {
  const title = [...parts.filter(Boolean), "RADaR"].join(" · ");
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => { document.title = prev; };
  }, [title]);
}

function AccountMenu() {
  const { identity, signOut } = useAuth();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const initial = (identity?.email ?? "?").charAt(0).toUpperCase();
  return (
    <>
      <IconButton onClick={(e) => setAnchor(e.currentTarget)} aria-label="Account menu" aria-haspopup="menu" size="small">
        <Avatar sx={(t) => ({ width: 32, height: 32, fontSize: 14, bgcolor: t.palette.tokens.primaryContainer, color: t.palette.tokens.onPrimaryContainer })}>
          {initial}
        </Avatar>
      </IconButton>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
        <MenuItem onClick={() => { setAnchor(null); signOut(); }}>Sign out</MenuItem>
      </Menu>
    </>
  );
}

/** Layout route: top app bar (logo, client name, account menu) + page content. */
export function PortalShell() {
  const { preview, links } = usePortal();
  const { client } = usePortalParams();
  const [names, setNames] = useState<Record<string, string>>({});
  const ctx = useMemo<ShellCtx>(() => ({ setClientName: (s, n) => setNames((m) => (m[s] === n ? m : { ...m, [s]: n })) }), []);
  const clientName = (client && (names[client] ?? clientNameOf(client))) || "";

  return (
    <Ctx.Provider value={ctx}>
      <Box sx={{ minHeight: preview ? `calc(100vh - ${PREVIEW_BAR_HEIGHT}px)` : "100vh", bgcolor: "background.default" }}>
        <AppBar position="sticky" sx={{ top: preview ? PREVIEW_BAR_HEIGHT : 0 }}>
          <Toolbar sx={{ gap: 2, minHeight: { xs: 56, sm: 64 } }}>
            <Box component={RouterLink} to={client ? links.client(client) : links.home} aria-label="RADaR home" sx={{ display: "flex", flexShrink: 0 }}>
              <Box component="img" src={LOGO} alt="RADaR" sx={{ height: 32, width: "auto", display: "block" }} />
            </Box>
            {clientName && (
              <Typography variant="subtitle1" noWrap sx={{ color: "text.secondary", borderLeft: 1, borderColor: "divider", pl: 2, minWidth: 0 }}>
                {clientName}
              </Typography>
            )}
            <Box sx={{ ml: "auto" }}>{!preview && <AccountMenu />}</Box>
          </Toolbar>
        </AppBar>
        <PageContainer><Outlet /></PageContainer>
      </Box>
    </Ctx.Provider>
  );
}

export function PageContainer({ children }: { children: ReactNode }) {
  return <Box component="main" sx={{ maxWidth: 1280, mx: "auto", px: { xs: 2, md: 4 }, py: { xs: 2.5, md: 4 } }}>{children}</Box>;
}

/** [Client] > [Project] > [View]. Last item is plain text; earlier ones are links. */
export function PortalBreadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <Breadcrumbs aria-label="Breadcrumb" sx={{ mb: 2 }}>
      {items.map((it, i) =>
        it.to && i < items.length - 1 ? (
          <Link key={i} component={RouterLink} to={it.to} underline="hover" color="text.secondary" variant="body2">{it.label}</Link>
        ) : (
          <Typography key={i} variant="body2" color="text.primary" aria-current={i === items.length - 1 ? "page" : undefined}>{it.label}</Typography>
        ),
      )}
    </Breadcrumbs>
  );
}
