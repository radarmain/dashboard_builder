import { useEffect, useState } from "react";
import { get, post, type Revision } from "../api";
import { Badge, Btn, ErrorBox, errMsg } from "../ui";

export function RevisionsDrawer({ viewPath, dirty, onClose, onRestored }: {
  viewPath: string; dirty: boolean; onClose: () => void; onRestored: () => Promise<void>;
}) {
  const [revs, setRevs] = useState<Revision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    get<Revision[]>(`${viewPath}/revisions`).then(setRevs).catch((e) => setError(errMsg(e)));
  }, [viewPath]);

  async function restore(n: number) {
    if (dirty && !window.confirm("Restoring replaces your unsaved edits. Continue?")) return;
    setBusy(n); setError(null);
    try {
      await post(`${viewPath}/revisions/${n}/restore`);
      await onRestored();
    } catch (e) { setError(errMsg(e)); setBusy(null); }
  }

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/20" onClick={onClose}>
      <aside className="h-full w-96 overflow-auto bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Revisions</h2>
          <Btn variant="ghost" onClick={onClose}>Close</Btn>
        </div>
        <p className="mb-3 text-xs text-slate-500">The last 10 saves. Restoring creates a new draft revision; old revisions are never changed.</p>
        <ErrorBox error={error} />
        {!revs && !error && <div className="text-sm text-slate-500">Loading…</div>}
        {revs?.length === 0 && <div className="text-sm text-slate-500">No revisions yet. Save to create one.</div>}
        <ul className="space-y-2">
          {revs?.map((r) => (
            <li key={r.revision} className="rounded-md border border-slate-200 p-3">
              <div className="flex items-center gap-2">
                <span className="font-medium">#{r.revision}</span>
                {r.is_published && <Badge tone="green">Published</Badge>}
                {r.is_draft && <Badge tone="blue">Current draft</Badge>}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {r.updated_at ? new Date(r.updated_at).toLocaleString() : "unknown time"} · {r.updated_by ?? "unknown"}
              </div>
              <Btn variant="secondary" className="mt-2" disabled={busy !== null || r.is_draft} onClick={() => restore(r.revision)}>
                {busy === r.revision ? "Restoring…" : "Restore as draft"}
              </Btn>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
