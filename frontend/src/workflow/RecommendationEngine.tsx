import { useCallback, useEffect, useRef, useState } from "react";
import { plans, workflow } from "../api";
import AiBadge from "../components/AiBadge";
import { ErrorNote } from "../components/Help";
import { DiffTables } from "../components/PlanBuilder";
import { hrefOf } from "../flow";
import { navigate } from "../router";
import KeyFigures, { TraceKpis, useBaselineTrace } from "../twin/KeyFigures";
import { num } from "../twin/chart";
import { usePlanPicker } from "../twin/TraceBar";
import type { StoredTrace } from "../twin/types";
import type { PlanAnalysis } from "../types";
import type { Rec, RecommendationSet } from "./types";

const KPI: { key: string; label: string }[] = [
  { key: "energy_per_inference", label: "Energy per input" },
  { key: "power_avg", label: "Average power" },
  { key: "latency", label: "Latency per input" },
  { key: "capacity_fps", label: "Throughput" },
  { key: "temperature", label: "Chip temperature" },
];

function pct(p: number | null) {
  if (p === null || !Number.isFinite(p)) return "n/a";
  if (Math.abs(p) < 0.5) return "no change";
  return `${p > 0 ? "+" : "−"}${Math.abs(p).toFixed(1)}%`;
}

function Feas({ s }: { s: string }) {
  return <span className={`chip chip-${s}`}>{s.replace(/_/g, " ")}</span>;
}

interface Pick { key: string; title: string; ids: string[]; recs: Rec[] }

/** The updated analysis for one picked change (or the recommended combination): verdict, numbers,
 * parameters and the same key figures as the analysis page, today next to the change. */
function UpdatedAnalysis({ projectId, version, runKey, pick, canWrite }: { projectId: number; version: number; runKey: number | null; pick: Pick; canWrite: boolean }) {
  const base = useBaselineTrace(projectId, version, runKey);
  const [a, setA] = useState<PlanAnalysis | null>(null);
  const [t, setT] = useState<StoredTrace | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showAll, setShowAll] = useState(false);
  const pickPlan = usePlanPicker();

  useEffect(() => {
    let live = true;
    setA(null);
    setT(null);
    setError(null);
    Promise.all([plans.analyse(projectId, pick.ids), plans.trace(projectId, pick.ids)])
      .then(([x, y]) => live && (setA(x), setT(y)))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [projectId, pick.key, pick.ids]);

  const rec = pick.recs.length === 1 ? pick.recs[0] : null;
  return (
    <div className="updated-analysis" aria-live="polite">
      <section className="panel">
        <h2>{pick.title}</h2>
        {rec && (
          <dl className="rec-kv">
            {rec.problem && <><dt>Problem it targets</dt><dd>{rec.problem}</dd></>}
            {rec.change && <><dt>The change</dt><dd>{rec.change}</dd></>}
            <dt>Why it may help</dt><dd>{rec.why}</dd>
            {rec.tradeoffs.length > 0 && <><dt>Trade-offs</dt><dd>{rec.tradeoffs.join(" ")}</dd></>}
          </dl>
        )}
        {!rec && <p className="muted">These changes together: {pick.recs.map((r) => r.title).join("; ")}.</p>}
      </section>

      <ErrorNote error={error} />
      {!a && !error && <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w90" /><div className="sk-line w60" /></div>}
      {a && (
        <>
          <h2 className="section-title">Updated analysis</h2>
          <div className="kpis">
            <div><span>Today</span><strong><Feas s={a.baseline.feasibility} /></strong><small>limited by {a.baseline.bottleneck}{a.baseline.throttled ? ", too hot" : ""}</small></div>
            <div><span>With this change</span><strong><Feas s={a.plan.feasibility} /></strong><small>limited by {a.plan.bottleneck}{a.plan.throttled ? ", too hot" : ""}</small></div>
            {KPI.map(({ key, label }) => {
              const m = a.diff.metrics.find((x) => x.name === key);
              return m ? (
                <div key={key}><span>{label}</span><strong className={`num ${m.better ? "good" : m.better === false ? "bad" : ""}`}>{pct(m.change_percent)}</strong>
                  <small>{num(m.baseline.value)} → {num(m.plan.value)} {m.unit}</small></div>
              ) : null;
            })}
          </div>
          <p className="note">{a.accuracy}</p>
          {a.applied.notes.map((n) => <p key={n} className="note-info">{n}</p>)}
          {a.directional.length > 0 && <p className="note">Directional what-if: {a.directional.join(", ")}. An analytical estimate, not a validated design.</p>}

          <section className="panel">
            <div className="row">
              <h3>What changes, in numbers</h3>
              <label className="check-row"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show unchanged parameters</label>
            </div>
            <DiffTables diff={a.diff} showAll={showAll} />
          </section>

          {t && base.data && (
            <>
              <h2 className="section-title">Figures: today and with this change</h2>
              <div className="compare-figs">
                <div><h4>Today</h4><TraceKpis t={base.data.trace} /></div>
                <div><h4>With this change</h4><TraceKpis t={t.trace} /></div>
              </div>
              <KeyFigures projectId={projectId} a={base.data} b={t} labels={["Today", "With this change"]} />
            </>
          )}
          {!t && !error && <div className="panel skeleton" aria-busy="true"><div className="sk-line w60" /></div>}

          <div className="next-cta">
            <p>Like this change? Keep it in a plan with others, or open every detailed analysis view with it applied.</p>
            <div className="actions">
              {pickPlan && (
                <button onClick={() => { pickPlan({ key: pick.key, label: pick.title, changeIds: pick.ids }); navigate(`/p/${projectId}/thermal`); }}>
                  Open the detailed views with it
                </button>
              )}
              <button className="primary" disabled={!canWrite} onClick={() => navigate(`/p/${projectId}/recommendations?tab=plan&add=${encodeURIComponent(pick.ids.join(","))}`)}>
                Add to a plan
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** Step 2 of the decision: every change the engine proposes, ranked. Picking one re-runs the engine
 * for today's setup with that change and shows the updated analysis. */
export default function RecommendationEngine({ projectId, version, runKey, canWrite }: { projectId: number; version: number; runKey: number | null; canWrite: boolean }) {
  const [set, setSet] = useState<RecommendationSet | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const result = useRef<HTMLDivElement>(null);
  // On narrow screens the list sits above the result: bring the result into view when a change is picked.
  useEffect(() => {
    if (pick && window.matchMedia("(max-width: 1360px)").matches) result.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [pick]);
  const load = useCallback(() => {
    setError(null);
    workflow.recommendations(projectId).then(setSet).catch(setError);
  }, [projectId]);
  useEffect(() => {
    load();
  }, [load, runKey]);

  if (error) return <ErrorNote error={error} onRetry={load} />;
  if (!set) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w90" /></div>;
  if (!set.analysed) return <section className="panel empty-state"><p>{set.message}</p></section>;

  const all = [...set.software, ...set.hardware];
  const runnable = all.filter((r) => r.plan_change_id).sort((x, y) => (x.rank ?? 999) - (y.rank ?? 999));
  const advisory = all.filter((r) => !r.plan_change_id);
  const recommended = runnable.filter((r) => r.in_recommended || r.status === "recommended");
  const choose = (r: Rec) => setPick({ key: r.id, title: r.title, ids: [r.plan_change_id!], recs: [r] });

  return (
    <>
      {set.stale && <p className="note-warn" role="status">The inputs changed after this analysis. Run the analysis again before acting on these.</p>}
      <p className="muted">
        Ranked for your requirements: changes that fix a failing requirement first, then your priority ({set.priority?.replace(/_/g, " ")}).
        Pick a change to see the analysis re-run with it. Every number is an engine prediction, not a measurement.
      </p>
      <div className="ai-callout">
        <AiBadge role="more ideas" />
        <p>
          Ask the language model for changes this list may have missed. It sees the engine's numbers, never your files, and its ideas
          stay labelled not evaluated until the engine runs them.{" "}
          <a href={`${hrefOf(projectId, { area: "recommendations", tab: "catalogue" })}&focus=ai`}>Ask the AI for candidates →</a>
        </p>
      </div>
      <div className="rec-engine">
        <nav className="rec-list" aria-label="Changes">
          {recommended.length > 1 && (
            <button className={`rec-item ${pick?.key === "recommended" ? "on" : ""}`} aria-pressed={pick?.key === "recommended"}
              onClick={() => setPick({ key: "recommended", title: "The recommended combination", ids: recommended.map((r) => r.plan_change_id!), recs: recommended })}>
              <strong>The recommended combination</strong>
              <small>{recommended.length} changes together</small>
            </button>
          )}
          {(["software", "hardware"] as const).map((cat) => {
            const items = runnable.filter((r) => r.category === cat);
            return items.length ? (
              <div key={cat} className="rec-list-group">
                <h3>{cat === "software" ? "Software" : "Hardware"}</h3>
                {items.map((r) => (
                  <button key={r.id} className={`rec-item ${pick?.key === r.id ? "on" : ""}`} aria-pressed={pick?.key === r.id} onClick={() => choose(r)}>
                    <strong>{r.rank ? `${r.rank}. ` : ""}{r.title}</strong>
                    <small>{r.area_label}{r.fixes?.length ? `, fixes ${r.fixes.join(", ")}` : ""}</small>
                    {(r.in_recommended || r.status === "recommended" || r.status === "directional") && (
                      <span className="rec-tags">
                        {(r.in_recommended || r.status === "recommended") && <span className="badge">recommended</span>}
                        {r.status === "directional" && <span className="badge badge-assumed">directional</span>}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            ) : null;
          })}
          {advisory.length > 0 && (
            <details className="rec-list-group">
              <summary>Guidance the engine cannot simulate ({advisory.length})</summary>
              <ul className="hint">{advisory.map((r) => <li key={r.id}><strong>{r.title}</strong>: {r.why}</li>)}</ul>
            </details>
          )}
        </nav>
        <div ref={result} className="rec-result">
          {pick ? (
            <UpdatedAnalysis projectId={projectId} version={version} runKey={runKey} pick={pick} canWrite={canWrite} />
          ) : (
            <section className="panel empty-state">
              <h2>Pick a change</h2>
              <p>Choose a change on the left. The engine re-runs the analysis of your setup with it applied and shows the new verdict, the numbers that move and the same figures as the analysis page, today next to the change.</p>
              {runnable[0] && <button className="primary" onClick={() => choose(runnable[0])}>Start with the top-ranked change</button>}
            </section>
          )}
        </div>
      </div>
    </>
  );
}
