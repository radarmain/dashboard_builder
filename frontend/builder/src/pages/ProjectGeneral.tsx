import { useEffect, useState } from "react";
import { Box, TextField, Typography } from "@mui/material";
import { patch, type Project } from "../api";
import { SlugFields } from "../SlugFields";
import { Btn, Card, ConfirmDialog, ErrorBox, errMsg, renameSlugError } from "../ui";

/** "General" card at the top of the project Settings tab: rename the project and/or change its URL slug. */
export function ProjectGeneral({ client, project, name: initName, onRenamed }: {
  client: string; project: string; name: string; onRenamed: (p: Project) => void;
}) {
  const [name, setName] = useState(initName);
  const [slug, setSlug] = useState(project);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setName(initName); setSlug(project); }, [initName, project]);

  const slugChanged = slug !== project;
  const nameChanged = name.trim() !== initName;
  const invalid = !name.trim() || (slugChanged && !!renameSlugError(slug));

  async function save() {
    setConfirm(false);
    setBusy(true);
    setError(null);
    try {
      const p = await patch<Project>(`/clients/${client}/projects/${project}`, {
        ...(nameChanged ? { name: name.trim() } : {}), ...(slugChanged ? { slug } : {}),
      });
      onRenamed(p);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card sx={{ mb: 3 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>General</Typography>
      <Box sx={{ display: "grid", gap: 2.5, maxWidth: 480 }}>
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <SlugFields name={name} slug={slug} onSlug={setSlug} />
        <ErrorBox error={error} />
        <Box>
          <Btn disabled={busy || invalid || (!slugChanged && !nameChanged)} onClick={() => (slugChanged ? setConfirm(true) : void save())}>
            {busy ? "Saving…" : "Save"}
          </Btn>
        </Box>
      </Box>
      <ConfirmDialog open={confirm} title="Change project URL?" confirmLabel="Change URL"
        body="The client link for every view in this project will change. Old links keep working."
        onConfirm={() => void save()} onCancel={() => setConfirm(false)} />
    </Card>
  );
}
