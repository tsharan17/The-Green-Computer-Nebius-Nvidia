import { useCallback, useEffect, useState } from "react";
import { download, history, workspace } from "../api";
import { when } from "../pages/WorkspacePage";
import type { ArtifactItem, AuditItem, RunItem, VersionItem } from "../types";

const ACTION_LABEL: Record<string, string> = {
  "project.created": "Project created",
  "project.version_created": "Inputs changed (new version)",
  "project.version_restored": "Older version restored",
  "project.archived": "Archived",
  "project.restored": "Restored from archive",
  "analysis.run": "Analysis run",
  "file.uploaded": "File uploaded",
  "file.deleted": "File deleted",
  "export.created": "Exported",
  "measurements.imported": "Measurements imported",
  "plan.saved": "Design plan saved",
  "plan.analysed": "Design plan analysed",
  "execution.finished": "Local execution finished",
};

function sizeOf(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export default function HistoryPanel({ projectId, canWrite, onRestored }: { projectId: number; canWrite: boolean; onRestored: () => void }) {
  const [versions, setVersions] = useState<VersionItem[] | null>(null);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [files, setFiles] = useState<ArtifactItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [v, r, a, f] = await Promise.all([
        history.versions(projectId),
        history.runs(projectId),
        workspace.audit(projectId),
        workspace.artifacts(projectId),
      ]);
      setVersions(v);
      setRuns(r);
      setAudit(a);
      setFiles(f);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [projectId]);
  useEffect(() => {
    load();
  }, [load]);

  async function restore(v: number) {
    try {
      await history.restore(projectId, v);
      onRestored();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="history">
      {error && <p className="error" role="alert">{error}</p>}
      <section className="panel" aria-labelledby="ver-h">
        <h2 id="ver-h">Input versions</h2>
        <p className="hint">Every change to the model, hardware, workload or requirements is saved as a new version. Versions are never edited; restoring one makes a new version.</p>
        {versions === null ? (
          <div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Version</th><th>Saved</th><th>System</th><th>Requirements</th><th>Note</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {versions.map((v, i) => (
                  <tr key={v.id}>
                    <td className="num">v{v.version}{i === 0 && <span className="badge"> current</span>}</td>
                    <td className="muted">{when(v.created_at)}</td>
                    <td>{v.spec.model_id} on {v.spec.device_id}</td>
                    <td className="muted">
                      {v.spec.workload.fps} fps, ≤{v.spec.constraints.max_power_w} W, {v.spec.workload.ambient_c} °C
                    </td>
                    <td className="muted">{v.note}</td>
                    <td>{i > 0 && canWrite && <button className="link" onClick={() => restore(v.version)}>Restore</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="run-h">
        <h2 id="run-h">Analyses</h2>
        {runs.length === 0 ? (
          <p className="empty">No analysis yet. Run one from Model and hardware.</p>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>When</th><th>Kind</th><th>Inputs</th><th>Result</th><th>Engine</th><th><span className="sr-only">Download</span></th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="muted">{when(r.created_at)}</td>
                    <td>{r.kind}</td>
                    <td className="num">{r.version ? `v${r.version}` : "–"}</td>
                    <td>
                      {typeof r.summary.feasibility === "string" && (
                        <span className={`chip chip-${r.summary.feasibility}`}>{String(r.summary.feasibility).replace(/_/g, " ")}</span>
                      )}
                      {typeof r.summary.best_feasibility === "string" && <span className="muted"> best: {String(r.summary.best_feasibility).replace(/_/g, " ")}</span>}
                    </td>
                    <td className="muted">{r.engine_version}</td>
                    <td>
                      <button className="link" onClick={() => download(`/projects/${projectId}/runs/${r.id}`, `run-${r.id}.json`)}>JSON</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="ws-grid">
        <section className="panel" aria-labelledby="files-h">
          <h2 id="files-h">Files</h2>
          {files.length === 0 ? (
            <p className="empty">No files uploaded to this project.</p>
          ) : (
            <ul className="plain-list">
              {files.map((f) => (
                <li key={f.id}>
                  <span className={`chip chip-${f.status === "valid" ? "predicted_feasible" : f.status === "invalid" ? "infeasible" : "uncertain"}`}>{f.status.replace("_", " ")}</span>{" "}
                  <span>{f.filename}</span> <span className="muted">{sizeOf(f.size_bytes)}, {when(f.created_at)}</span>
                  {f.stored && <button className="link" onClick={() => download(`/artifacts/${f.id}/download`, f.filename)}>Download</button>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel" aria-labelledby="aud-h">
          <h2 id="aud-h">Audit trail</h2>
          {audit.length === 0 ? (
            <p className="empty">Nothing recorded yet.</p>
          ) : (
            <ol className="audit">
              {audit.map((a) => (
                <li key={a.id}>
                  <time className="muted">{when(a.created_at)}</time>
                  <span>{ACTION_LABEL[a.action] ?? a.action}</span>
                  {typeof a.detail.filename === "string" && <span className="muted"> {a.detail.filename}</span>}
                  {typeof a.detail.what === "string" && <span className="muted"> {a.detail.what}</span>}
                  {typeof a.detail.version === "number" && <span className="muted"> v{a.detail.version}</span>}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
