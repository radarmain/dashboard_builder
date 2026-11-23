import { Link, Outlet, useLocation } from "react-router-dom";
import { Box, Chip, List, ListItemButton, ListItemText, Typography } from "@mui/material";
import { useAuth } from "@rdb/core";
import { Btn, LOGO } from "@rdb/core";
import { AlertBanner } from "./AlertBanner";

/** Navigation-drawer-style sidebar (pill-shaped selected indicator) + routed content. */
export function Layout() {
  const { identity, signOut } = useAuth();
  const { pathname } = useLocation();
  const [, c, client, p, project, v, view] = pathname.split("/"); // /c/:client/p/:project/v/:view
  const crumbs: { to: string; label: string; kind: string }[] = [];
  if (c === "c" && client) crumbs.push({ to: `/c/${client}`, label: client, kind: "Client" });
  if (p === "p" && project) crumbs.push({ to: `/c/${client}/p/${project}`, label: project, kind: "Project" });
  if (v === "v" && view) crumbs.push({ to: pathname, label: view, kind: "View" });

  const isAdmin = !!identity?.groups.includes("admin");
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
    {isAdmin && <AlertBanner />}
    <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
      <Box
        component="aside"
        sx={{
          width: 280, flexShrink: 0, display: "flex", flexDirection: "column", p: 1.5,
          bgcolor: (t) => t.palette.tokens.surfaceContainerLow, borderRight: 1, borderColor: "divider",
        }}
      >
        <Box component={Link} to="/" sx={{ display: "block", px: 2, pt: 2, pb: 1 }} aria-label="RADaR Dashboard Builder home">
          <Box component="img" src={LOGO} alt="RADaR" sx={{ display: "block", height: 40, width: "auto" }} />
        </Box>
        <Typography variant="caption" sx={{ px: 2, pb: 2, color: "text.secondary", letterSpacing: 0.5 }}>Dashboard Builder</Typography>

        <List component="nav" sx={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-start", gap: 0.25, overflow: "auto" }}>
          <ListItemButton component={Link} to="/" selected={pathname === "/"}>
            <ListItemText primary="Clients" slotProps={{ primary: { variant: "body2", sx: { fontWeight: 500, fontSize: "0.8125rem" } } }} />
          </ListItemButton>
          {crumbs.map((cr, i) => (
            <ListItemButton key={cr.to} component={Link} to={cr.to} selected={i === crumbs.length - 1} sx={{ ml: `${(i + 1) * 12}px` }}>
              <ListItemText
                primary={cr.label}
                secondary={cr.kind}
                slotProps={{ primary: { variant: "body2", noWrap: true, sx: { fontWeight: 500, fontSize: "0.8125rem", lineHeight: "18px" } }, secondary: { variant: "caption", sx: { fontSize: "0.6875rem", lineHeight: "14px" } } }}
              />
            </ListItemButton>
          ))}
        </List>

        <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1.5 }}>
          {isAdmin && (
            <ListItemButton component={Link} to="/settings" selected={pathname === "/settings"} sx={{ mb: 0.5 }}>
              <ListItemText primary="Settings" slotProps={{ primary: { variant: "body2", sx: { fontWeight: 500, fontSize: "0.8125rem" } } }} />
            </ListItemButton>
          )}
          <Box sx={{ px: 1 }}>
          <Typography variant="body2" noWrap title={identity?.email} sx={{ color: "text.primary" }}>{identity?.email}</Typography>
          <Box sx={{ display: "flex", gap: 0.5, my: 0.75, flexWrap: "wrap" }}>
            {identity?.groups.map((g) => <Chip key={g} size="small" label={g} sx={{ height: 22 }} />)}
          </Box>
          <Btn variant="ghost" size="small" onClick={signOut} sx={{ ml: -1.5 }}>Sign out</Btn>
          </Box>
        </Box>
      </Box>
      <Box component="main" sx={{ minWidth: 0, flex: 1, overflow: "auto" }}><Outlet /></Box>
    </Box>
    </Box>
  );
}
