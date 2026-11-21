// Generic UI lives in @rdb/core; only Builder-specific helpers are defined here.
import { Badge } from "@rdb/core";

export { Badge, Btn, Card, ConfirmDialog, ErrorBox, LOGO, Loading, PageHeader, errMsg, monoSx } from "@rdb/core";

export const SLUG_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function slugify(s: string): string {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);
}

export function viewStatus(v: { draft_revision: number | null; published_revision: number | null; has_unpublished_changes: boolean }) {
  if (v.published_revision === null) return <Badge tone="gray">Draft</Badge>;
  if (v.has_unpublished_changes) return <Badge tone="amber">Unpublished changes</Badge>;
  return <Badge tone="green">Published</Badge>;
}
