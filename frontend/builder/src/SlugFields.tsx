import { useEffect, useState } from "react";
import { Checkbox, FormControlLabel, TextField } from "@mui/material";
import { RENAME_SLUG_MAX, monoSx, renameSlugError, slugify } from "./ui";

export const SLUG_HELP = "Part of the client link. Changing it keeps the old link working.";

/** Slug field + "Update the URL to match the name" (off by default; regenerates the slug as you type). */
export function SlugFields({ name, slug, onSlug, disabledReason }: {
  name: string; slug: string; onSlug: (s: string) => void; disabledReason?: string;
}) {
  const [follow, setFollow] = useState(false);
  useEffect(() => { if (follow) onSlug(slugify(name, RENAME_SLUG_MAX)); }, [follow, name]); // eslint-disable-line react-hooks/exhaustive-deps
  const err = disabledReason ? null : renameSlugError(slug);
  return (
    <>
      <TextField label="Slug" value={slug} disabled={!!disabledReason} error={!!err}
        helperText={disabledReason ?? err ?? SLUG_HELP}
        slotProps={{ input: { sx: monoSx } }}
        onChange={(e) => { setFollow(false); onSlug(e.target.value); }} />
      <FormControlLabel disabled={!!disabledReason} sx={{ mt: -1.5 }}
        control={<Checkbox size="small" checked={follow} onChange={(e) => setFollow(e.target.checked)} />}
        label="Update the URL to match the name" />
    </>
  );
}
