import { Link, Outlet, useLocation } from "react-router-dom";
import { Box, Chip, List, ListItemButton, ListItemText, Typography } from "@mui/material";
import { useAuth } from "@rdb/core";
import { Btn, LOGO } from "@rdb/core";

/** Navigation-drawer-style sidebar (pill-shaped selected indicator) + routed content. */
export function Layout() {
  const { identity, signOut } = useAuth();
  const { pathname } = useLocation();
  const [, c, client, p, project, v, view] = pathname.split("/"); // /c/:client/p/:project/v/:view
  const crumbs: { to: string; label: string; kind: string }[] = [];
  if (c === "c" && client) crumbs.push({ to: `/c/${client}`, label: client, kind: "Client" });
  if (p === "p" && project) crumbs.push({ to: `/c/${client}/p/${project}`, label: project, kind: "Project" });
  if (v === "v" && view) crumbs.push({ to: pathname, label: view, kind: "View" });

  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
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

        <List component="nav" sx={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-start", gap: 0.5, overflow: "auto" }}>
          <ListItemButton component={Link} to="/" selected={pathname === "/"}>
            <ListItemText primary="Clients" slotProps={{ primary: { variant: "subtitle2" } }} />
          </ListItemButton>
          {identity?.groups.includes("admin") && (
            <ListItemButton component={Link} to="/settings" selected={pathname === "/settings"}>
              <ListItemText primary="Settings" slotProps={{ primary: { variant: "subtitle2" } }} />
            </ListItemButton>
          )}
          {crumbs.map((cr, i) => (
            <ListItemButton key={cr.to} component={Link} to={cr.to} selected={i === crumbs.length - 1} sx={{ ml: `${(i + 1) * 12}px` }}>
              <ListItemText
                primary={cr.label}
                secondary={cr.kind}
                slotProps={{ primary: { variant: "subtitle2", noWrap: true }, secondary: { variant: "caption" } }}
              />
            </ListItemButton>
          ))}
        </List>

        <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1.5, px: 1 }}>
          <Typography variant="body2" noWrap title={identity?.email} sx={{ color: "text.primary" }}>{identity?.email}</Typography>
          <Box sx={{ display: "flex", gap: 0.5, my: 0.75, flexWrap: "wrap" }}>
            {identity?.groups.map((g) => <Chip key={g} size="small" label={g} sx={{ height: 22 }} />)}
          </Box>
          <Btn variant="ghost" size="small" onClick={signOut} sx={{ ml: -1.5 }}>Sign out</Btn>
        </Box>
      </Box>
      <Box component="main" sx={{ minWidth: 0, flex: 1, overflow: "auto" }}><Outlet /></Box>
    </Box>
  );
}
