import { useEffect, useState } from "react";
import { plans } from "../api";
import { when } from "../pages/WorkspacePage";
import type { SavedPlan } from "../types";
import { PlanResult } from "./PlanBuilder";

export default function SavedPlans({ projectId, refreshKey }: { projectId: number; refreshKey: number }) {
  const [list, setList] = useState<SavedPlan[] | null>(null);
  const [open, setOpen] = useState<SavedPlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    plans.list(projectId).then(setList).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [projectId, refreshKey]);

  async function show(id: number) {
    try {
      setOpen(await plans.get(projectId, id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!list) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w60" /></div>;
  if (!list.length)
    return (
      <section className="panel empty-state">
        <h2>No saved plans yet</h2>
        <p>Build a plan from the recommendations, analyse it and save it. Saved plans are versioned and can be exported.</p>
      </section>
    );
  return (
    <div className="saved-plans">
      <section className="panel">
        <h2>Saved plans</h2>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Plan</th><th>Changes</th><th>Result</th><th className="num">Energy</th><th>Saved</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>
              {list.map((p) => {
                const e = p.summary.energy_per_inference;
                const pct = typeof e === "object" && e && e.change_percent !== null ? `${e.change_percent > 0 ? "+" : ""}${e.change_percent.toFixed(1)}%` : "–";
                return (
                  <tr key={p.id} className={open?.id === p.id ? "selected" : ""}>
                    <td><strong>{p.name}</strong> <span className="muted">v{p.version}</span>{p.note && <small className="block muted">{p.note}</small>}</td>
                    <td className="muted">{p.changes.map((c) => c.title).join("; ")}</td>
                    <td>{typeof p.summary.plan_feasibility === "string" && <span className={`chip chip-${p.summary.plan_feasibility}`}>{String(p.summary.plan_feasibility).replace(/_/g, " ")}</span>}</td>
                    <td className="num">{pct}</td>
                    <td className="muted">{when(p.created_at)}{p.project_version ? `, inputs v${p.project_version}` : ""}</td>
                    <td><button className="link" onClick={() => show(p.id)}>{open?.id === p.id ? "Shown below" : "Open"}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      {open?.analysis && (
        <>
          <h2 className="section-title">{open.name}, version {open.version}</h2>
          <PlanResult a={open.analysis} />
        </>
      )}
    </div>
  );
}
