import { useCallback, useEffect, useState } from "react";
import { workflow } from "../api";
import { ErrorNote } from "../components/Help";
import type { CheckStatus, VerificationReport } from "./types";

const AREA_TITLE: Record<string, string> = {
  model: "AI model", hardware: "Hardware and cooling", workload: "Workload", requirements: "Requirements",
  consistency: "Model, hardware and requirements together", files: "Uploaded files",
};
const STATUS_TEXT: Record<CheckStatus, string> = { pass: "Pass", warn: "Check", fail: "Fix", info: "Note" };

/** The Verification and Requirements Manager's report: what was checked, what is missing, and how
 * to fix it. It checks presence, format and consistency only, and says so. */
export default function VerificationPanel({ projectId, refreshKey }: { projectId: number; refreshKey: string }) {
  const [v, setV] = useState<VerificationReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => {
    setError(null);
    workflow.verification(projectId).then(setV).catch(setError);
  }, [projectId]);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (error) return <ErrorNote error={error} onRetry={load} />;
  if (!v) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w60" /><div className="sk-line w90" /></div>;
  const areas = [...new Set(v.checks.map((c) => c.area))];
  return (
    <section className="panel" aria-labelledby="ver-h">
      <div className="row">
        <h2 id="ver-h">Verification report</h2>
        <span className={`ver-status ver-${v.status}`} role="status">
          {v.ready_for_analysis ? (v.status === "pass" ? "Ready for analysis" : "Ready, with warnings") : "Not ready: fix the items marked Fix"}
        </span>
      </div>
      <p className="muted">
        {v.counts.pass} passed, {v.counts.warn} to check, {v.counts.fail} to fix, {v.counts.info} notes. Inputs version {v.inputs.project_version}.
      </p>
      {areas.map((a) => (
        <div key={a} className="ver-group">
          <h3>{AREA_TITLE[a] ?? a}</h3>
          <div className="table-wrap">
            <table className="tbl ver-table">
              <thead><tr><th scope="col">Result</th><th scope="col">Finding</th><th scope="col">What to do</th></tr></thead>
              <tbody>
                {v.checks.filter((c) => c.area === a).map((c) => (
                  <tr key={c.id}>
                    <td><span className={`ver-chip ver-${c.status}`}>{STATUS_TEXT[c.status]}</span></td>
                    <td>{c.message}</td>
                    <td className="muted">{c.fix || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <p className="hint">{v.note}</p>
    </section>
  );
}
