import { Link as RouterLink, Navigate } from "react-router-dom";
import { Box, Card, CardActionArea, Skeleton, Typography } from "@mui/material";
import { useAuth, isBuilder } from "@rdb/core";
import { portalApi, usePortal } from "@rdb/portal";
import { useEffect, useState } from "react";

type Clients = { slug: string; name: string }[];

/** "/" : send a client user to their client; pick if several; explain if none or if a builder. */
export function Home() {
  const { identity } = useAuth();
  const { links } = usePortal();
  const builder = !!identity && isBuilder(identity);
  const [clients, setClients] = useState<Clients | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    document.title = "RADaR";
    if (builder) return;
    portalApi.me().then(setClients, () => setFailed(true));
  }, [builder]);

  if (builder)
    return <Message>Use Client Preview in the Builder, or open a client URL directly.</Message>;
  if (failed) return <Message>Something went wrong loading your account. Please try again.</Message>;
  if (!clients) return <Skeleton variant="rounded" height={120} sx={{ borderRadius: "20px" }} />;
  if (clients.length === 0) return <Message>Your account isn't linked to a client yet.</Message>;
  if (clients.length === 1) return <Navigate to={links.client(clients[0].slug)} replace />;
  return (
    <>
      <Typography variant="h4" component="h1" sx={{ mb: 3 }}>Choose a client</Typography>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(3, 1fr)" } }}>
        {clients.map((c) => (
          <Card key={c.slug} variant="elevation" sx={(t) => ({ borderRadius: "20px", boxShadow: "none", bgcolor: t.palette.tokens.surfaceContainerLow })}>
            <CardActionArea component={RouterLink} to={links.client(c.slug)} sx={{ p: 3, borderRadius: "20px" }}>
              <Typography variant="h6">{c.name}</Typography>
            </CardActionArea>
          </Card>
        ))}
      </Box>
    </>
  );
}

function Message({ children }: { children: string }) {
  return (
    <Box sx={(t) => ({ py: 8, px: 3, textAlign: "center", borderRadius: "20px", bgcolor: t.palette.tokens.surfaceContainerLow })}>
      <Typography color="text.secondary">{children}</Typography>
    </Box>
  );
}
