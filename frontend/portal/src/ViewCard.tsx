import { Link as RouterLink } from "react-router-dom";
import { Box, Card, CardActionArea, Chip, Skeleton, Typography } from "@mui/material";
import { usePortal } from "./context";
import { KindIcon } from "./KindIcon";
import type { ViewCardData } from "./api";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function metaChips(c: ViewCardData["counts"]): string[] {
  const out: string[] = [];
  if (c.chart) out.push(plural(c.chart, "chart", "charts"));
  if (c.table) out.push(plural(c.table, "table", "tables"));
  if (c.kpi) out.push(plural(c.kpi, "KPI", "KPIs"));
  return out;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** M3 elevated/tonal card; the whole card is one link (keyboard accessible, state layer, teal focus ring). */
export function ViewCard({ client, view }: { client: string; view: ViewCardData }) {
  const { links } = usePortal();
  const chips = metaChips(view.counts);
  const icons = [...view.chart_kinds, ...(view.counts.table ? ["table"] : []), ...(view.counts.kpi ? ["kpi"] : [])];
  const updated = fmtDate(view.published_at);
  return (
    <Card variant="elevation" sx={(t) => ({ borderRadius: "20px", overflow: "hidden", border: "none", bgcolor: t.palette.tokens.surfaceContainerLow, height: "100%", boxShadow: "none" })}>
      <CardActionArea
        component={RouterLink}
        to={links.view(client, view.project_slug, view.slug)}
        sx={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "stretch", justifyContent: "flex-start", borderRadius: "20px" }}
      >
        <Box
          aria-hidden
          sx={(t) => ({
            height: 88, display: "flex", alignItems: "center", gap: 1.5, px: 2.5,
            bgcolor: t.palette.tokens.primaryContainer, color: t.palette.tokens.onPrimaryContainer,
          })}
        >
          {(icons.length ? icons : ["dashboard"]).slice(0, 6).map((k, i) => <KindIcon key={`${k}${i}`} kind={k} />)}
        </Box>
        <Box sx={{ p: 2.5, display: "flex", flexDirection: "column", gap: 1, flex: 1, width: "100%" }}>
          <Typography variant="h6" component="h3" sx={{ wordBreak: "break-word" }}>{view.name}</Typography>
          {view.description && (
            <Typography variant="body2" color="text.secondary" sx={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
              {view.description}
            </Typography>
          )}
          <Box sx={{ mt: "auto", pt: 1, display: "flex", flexWrap: "wrap", gap: 0.75, alignItems: "center" }}>
            {chips.map((c) => (
              <Chip key={c} size="small" label={c} sx={(t) => ({ bgcolor: t.palette.tokens.secondaryContainer, color: t.palette.tokens.onSecondaryContainer })} />
            ))}
            {updated && <Typography variant="caption" color="text.secondary" sx={{ ml: chips.length ? 0.5 : 0 }}>Updated {updated}</Typography>}
          </Box>
        </Box>
      </CardActionArea>
    </Card>
  );
}

export const cardGridSx = { display: "grid", gap: 2.5, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(3, 1fr)" } };

export function CardSkeletons({ n = 3 }: { n?: number }) {
  return (
    <Box sx={cardGridSx} aria-busy aria-label="Loading dashboards">
      {Array.from({ length: n }, (_, i) => <Skeleton key={i} variant="rounded" height={220} sx={{ borderRadius: "20px" }} />)}
    </Box>
  );
}
