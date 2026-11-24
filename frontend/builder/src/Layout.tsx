import { Link, Outlet, useLocation } from "react-router-dom";
import { Box, Chip, ListItemButton, ListItemText, Typography } from "@mui/material";
import { useAuth } from "@rdb/core";
import { Btn, LOGO } from "@rdb/core";
import { AlertBanner } from "./AlertBanner";
import { SidebarTree } from "./SidebarTree";
import { Notifier } from "./Lifecycle";

/** Navigation-drawer-style sidebar (pill-shaped selected indicator) + routed content. */
export function Layout() {
  const { identity, signOut } = useAuth();
  const { pathname } = useLocation();
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

        <SidebarTree />

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
      <Notifier />
    </Box>
    </Box>
  );
}
