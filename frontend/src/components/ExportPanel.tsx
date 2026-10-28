import { useCallback, useEffect, useState } from "react";
import { download, handoff, type ManifestCheck, type OptimisedModel, plans } from "../api";
import { when } from "../pages/WorkspacePage";
import type { ExportCatalogItem, ExportItem, SavedPlan } from "../types";
import { ErrorNote, Level } from "./Help";

const LABEL_CLASS: Record<string, string> = {
  generated_validated: "badge-measured",
  generated: "badge-analytical",
  template: "badge-assumed",
};

// Who each file is for, in the order people usually need them.
const GROUPS: { id: string; title: string; blurb: string; kinds: string[] }[] = [
  { id: "start", title: "Start here", blurb: "The short answer for decision makers, and everything an engineering team needs to take over.",
    kinds: ["founder_pdf", "founder_md", "handoff_zip"] },
  { id: "reports", title: "Engineering reports", blurb: "Every number with its range and source.",
    kinds: ["engineering_full_pdf", "engineering_full_md", "report_pdf", "report_html", "report_md"] },
  { id: "plan", title: "Plan, parameters and hardware", blurb: "What changes, old against new, and the hardware specification.",
    kinds: ["plan_json", "comparison_csv", "comparison_json", "hardware_changes_yaml", "plan_package_zip", "recommendations_json", "recommendations_csv"] },
  { id: "data", title: "Data, evidence and reproducibility", blurb: "The graph, the evidence behind each number, and a manifest that recomputes them.",
    kinds: ["system_graph_json", "evidence_zip", "manifest_json"] },
  { id: "run", title: "Run it yourself", blurb: "Templates to adapt on your device; nothing in them was executed here.",
    kinds: ["deployment_yaml", "recipe_zip"] },
  { id: "models", title: "Optimised model files", blurb: "Only transformations that really run and are checked here are offered.",
    kinds: ["int8_model"] },
];

function size(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(2)} MB`;
}

export default function ExportPanel({ projectId, canWrite }: { projectId: number; canWrite: boolean }) {
  const [catalog, setCatalog] = useState<ExportCatalogItem[] | null>(null);
  const [optimised, setOptimised] = useState<OptimisedModel[]>([]);
  const [saved, setSaved] = useState<SavedPlan[]>([]);
  const [planId, setPlanId] = useState<number | null>(null);
  const [history, setHistory] = useState<ExportItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [check, setCheck] = useState<ManifestCheck | null>(null);

  const load = useCallback(async () => {
    const [c, p, h, o] = await Promise.all([plans.exportCatalog(projectId), plans.list(projectId).catch(() => []), plans.exports(projectId),
      handoff.optimised().catch(() => [])]);
    setCatalog(c);
    setSaved(p);
    setHistory(h);
    setOptimised(o);
    setPlanId((cur) => cur ?? p[0]?.id ?? null);
  }, [projectId]);
  useEffect(() => {
    load().catch(setError);
  }, [load]);

  async function make(item: ExportCatalogItem) {
    setBusy(item.kind);
    setMsg(null);
    try {
      const row = await plans.createExport(projectId, item.kind, item.needs_plan || planId ? planId : null);
      await download(`/projects/${projectId}/exports/${row.id}/download`, row.filename);
      const v = row.validation as { worst_cosine?: number; not_measured?: string };
      setMsg({ ok: true, text: `${row.filename}: ${row.label_text}.${v.worst_cosine !== undefined ? ` Worst output cosine ${v.worst_cosine.toFixed(4)}. ${v.not_measured}` : ""}` });
      setHistory(await plans.exports(projectId));
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function verify(file: File) {
    setCheck(null);
    try {
      setCheck(await handoff.verify(JSON.parse(await file.text())));
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    }
  }

  if (error) return <section className="panel"><h2>Export</h2><ErrorNote error={error} onRetry={() => load().catch(setError)} /></section>;
  if (!catalog) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /></div>;
  const byKind = new Map(catalog.map((c) => [c.kind, c]));
  const placed = new Set(GROUPS.flatMap((g) => g.kinds));
  const extra = catalog.filter((c) => !placed.has(c.kind));

  const row = (c: ExportCatalogItem) => {
    const blocked = !c.available ? c.reason : c.needs_plan && !planId ? "Choose a saved design plan above (build one under Recommendations)." : null;
    return (
      <li key={c.kind}>
        <div>
          <strong>{c.name}</strong> <span className={`badge ${LABEL_CLASS[c.label]}`}>{c.label_text}</span>
          {blocked && <small className="block muted">{blocked}</small>}
        </div>
        <button disabled={!!blocked || !!busy || !canWrite} onClick={() => make(c)}>
          {busy === c.kind ? (c.kind === "int8_model" ? "Quantising and validating…" : "Preparing…") : "Download"}
        </button>
      </li>
    );
  };

  return (
    <div className="exports">
      <section className="panel">
        <h2>Export</h2>
        <p className="hint">
          Every file says what it is: <span className="badge badge-measured">generated and validated</span> was produced here and checked by
          running it; <span className="badge badge-analytical">generated</span> comes from your stored analysis;{" "}
          <span className="badge badge-assumed">template</span> is instructions or scripts for you to adapt and run. No changed PCB, RTL or chip
          file is ever produced.
        </p>
        <label className="field inline">
          Design plan
          <select value={planId ?? ""} onChange={(e) => setPlanId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">None (latest analysis only)</option>
            {saved.map((p) => <option key={p.id} value={p.id}>{p.name} v{p.version}</option>)}
          </select>
        </label>
        {msg && <p className={msg.ok ? "note-ok" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
        {GROUPS.map((g) => {
          const items = g.kinds.map((k) => byKind.get(k)).filter((x): x is ExportCatalogItem => !!x);
          return (
            <div key={g.id} className="export-group">
              <h3>{g.title}</h3>
              <p className="hint">{g.blurb}</p>
              <ul className="export-list">
                {items.map(row)}
                {g.id === "models" && optimised.filter((o) => o.status === "not offered").map((o) => (
                  <li key={o.kind} className="not-offered">
                    <div><strong>{o.name}</strong> <span className="badge">not offered</span><small className="block muted">{o.how}</small></div>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
        {extra.length > 0 && <ul className="export-list">{extra.map(row)}</ul>}
        <Level kind="engineering" title="Check a reproducibility manifest">
          <p className="hint">Recomputes every headline number from the manifest's captured inputs with this engine and compares them. The same check runs offline with scripts/reproduce.py.</p>
          <input type="file" accept=".json,application/json" aria-label="Reproducibility manifest" onChange={(e) => e.target.files?.[0] && verify(e.target.files[0])} />
          {check && (
            <p role="status" className={check.ok ? "note-ok" : "error"}>
              {check.ok ? `✓ Reproduced: ${check.compared} numbers match.` : `✕ Not reproduced (${check.compared} compared): ${check.problems.join("; ")}`}
            </p>
          )}
        </Level>
      </section>
      <section className="panel">
        <h2>Export history</h2>
        {history.length === 0 ? (
          <p className="empty">Nothing exported yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>File</th><th>Kind</th><th className="num">Size</th><th>When</th><th>SHA-256</th><th><span className="sr-only">Download</span></th></tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td>{h.filename}</td>
                    <td><span className={`badge ${LABEL_CLASS[h.label]}`}>{h.label_text}</span></td>
                    <td className="num">{size(h.size_bytes)}</td>
                    <td className="muted">{when(h.created_at)}</td>
                    <td className="muted mono-hash" title={h.sha256}>{h.sha256.slice(0, 12)}</td>
                    <td><button className="link" onClick={() => download(`/projects/${projectId}/exports/${h.id}/download`, h.filename)}>Download</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
