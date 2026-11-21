import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Box, Button, Tooltip, Typography } from "@mui/material";
import { PREVIEW_BAR_HEIGHT, PortalProvider } from "@rdb/portal";

const RETURN_KEY = "rdb.previewReturnTo";
export const PREVIEW_BASE = "/preview";
export const PREVIEW_TOOLTIP = "Shows the published version, exactly what clients see";

/** "Client Preview" link. Opens a portal path in preview mode and remembers where to return to. */
export function ClientPreviewButton({ to, size }: { to: string; size?: "small" | "medium" }) {
  const { pathname, search } = useLocation();
  return (
    <Tooltip title={PREVIEW_TOOLTIP}>
      {/* A real <a href> so the editor's unsaved-changes guard sees the click. */}
      <Button component={Link} to={to} state={{ returnTo: pathname + search }} variant="tonal" size={size}>
        Client Preview
      </Button>
    </Tooltip>
  );
}

/** Copies a public portal URL. Works once DNS is live; the view must be published and the client user needs access. */
export function CopyLinkButton({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Tooltip title={`${url}. Clients can open it only if the view is published and their user has access to this client.`}>
      <Button variant="tonal" onClick={() => { void navigator.clipboard.writeText(url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); }}>
        {copied ? "Copied" : label}
      </Button>
    </Tooltip>
  );
}

/**
 * Preview shell: ONE skinny header, then the portal (same components the Viewer uses) on the
 * analyst's own session. No sidebar or other Builder chrome.
 */
export function PreviewShell() {
  const { state, pathname } = useLocation();
  const stateReturn = (state as { returnTo?: string } | null)?.returnTo;

  useEffect(() => {
    if (stateReturn) sessionStorage.setItem(RETURN_KEY, stateReturn); // survives reloads and in-preview navigation
  }, [stateReturn]);

  const fallback = `/${pathname.split("/").slice(2, 4).join("/")}`; // /preview/c/:client/... -> /c/:client
  const returnTo = stateReturn ?? sessionStorage.getItem(RETURN_KEY) ?? (fallback.startsWith("/c/") ? fallback : "/");

  return (
    <PortalProvider basePath={PREVIEW_BASE} preview>
      <Box
        sx={(t) => ({
          position: "fixed", top: 0, left: 0, right: 0, height: PREVIEW_BAR_HEIGHT, zIndex: t.zIndex.appBar + 1,
          display: "flex", alignItems: "center", justifyContent: "space-between", px: 2,
          bgcolor: t.palette.tokens.tertiaryContainer, color: t.palette.tokens.onTertiaryContainer,
        })}
      >
        <Typography variant="caption" sx={{ fontWeight: 500, color: "inherit" }}>Preview Mode</Typography>
        <Button
          component={Link} to={returnTo} size="small" color="inherit"
          onClick={() => sessionStorage.removeItem(RETURN_KEY)}
          sx={{ minHeight: 24, py: 0, px: 1, fontSize: 12, color: "inherit" }}
        >
          Close Preview
        </Button>
      </Box>
      <Box sx={{ pt: `${PREVIEW_BAR_HEIGHT}px` }}><Outlet /></Box>
    </PortalProvider>
  );
}
