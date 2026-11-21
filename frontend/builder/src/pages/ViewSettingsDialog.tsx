import { useState } from "react";
import { Dialog, DialogActions, DialogContent, DialogTitle, TextField } from "@mui/material";
import { patch, type ViewSummary } from "../api";
import { Btn, ErrorBox, errMsg } from "../ui";

/** Name + description. Saves immediately via PATCH (metadata, not part of a revision). */
export function ViewSettingsDialog({ viewPath, name: initName, description: initDesc, onClose, onSaved }: {
  viewPath: string; name: string; description: string; onClose: () => void; onSaved: (v: Partial<ViewSummary>) => void;
}) {
  const [name, setName] = useState(initName);
  const [description, setDescription] = useState(initDesc);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const v = await patch<ViewSummary>(viewPath, { name: name.trim(), description: description.trim() });
      onSaved(v);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth slotProps={{ paper: { component: "form", onSubmit: submit } }}>
      <DialogTitle>View settings</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2.5, pt: "8px !important" }}>
        <TextField label="Name" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        <TextField label="Description (optional)" multiline minRows={3} maxRows={6} value={description}
          onChange={(e) => setDescription(e.target.value.slice(0, 300))}
          helperText={`Visible to clients. Changes apply immediately, no Publish needed. ${description.length}/300`} />
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <Btn type="submit" disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save"}</Btn>
      </DialogActions>
    </Dialog>
  );
}
