import { useEffect, useState } from "react";
import { Box, Link, Typography } from "@mui/material";
import { Btn } from "@rdb/core";
import { portalApi, viewerClientUrl, type ClientRef } from "@rdb/portal";

/** Access-denied screen content for CLIENT-group users only (never mounted for anyone else). */
export function ClientDashboards() {
  const [clients, setClients] = useState<ClientRef[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { portalApi.me().then(setClients, () => setFailed(true)); }, []);

  if (failed || !clients) return null;
  if (clients.length === 0)
    return <Typography variant="body2" color="text.secondary">Your account isn't linked to a client yet. Contact your RADaR team.</Typography>;
  if (clients.length === 1)
    return (
      <>
        <Typography variant="body2" color="text.secondary">Your dashboards are at the link below.</Typography>
        <Box><Btn component="a" href={viewerClientUrl(clients[0].slug)}>Open my dashboards</Btn></Box>
      </>
    );
  return (
    <Box sx={{ display: "grid", gap: 0.5 }}>
      <Typography variant="body2" color="text.secondary">Your dashboards are at the links below.</Typography>
      {clients.map((c) => <Link key={c.slug} href={viewerClientUrl(c.slug)}>{c.name || c.slug}</Link>)}
    </Box>
  );
}
