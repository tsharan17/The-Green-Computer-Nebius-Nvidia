import { download } from "../api";
import { Checks, MetricTable, configLabel } from "../components/common";
import { Level, Outcome, Why } from "../components/Help";
import { BOTTLENECK_PLAIN } from "../lib/glossary";
import KeyFigures, { TraceKpis, useBaselineTrace } from "../twin/KeyFigures";
import type { OptimisationResult, ProjectSpec } from "../types";

const TERM_FOR: Record<string, string> = { compute: "compute-bound", memory: "memory-bound", overhead: "overhead-bound", heat: "heat" };

/** Bars against a limit: today vs with the plan. A simple diagram for the "Explain" level. */
function LimitBars({ rows }: { rows: { label: string; unit: string; today: number; plan: number | null; limit: number | null; lower: boolean }[] }) {
  return (
    <div className="limit-bars" role="table" aria-label="Today and with the plan, against your limits">
      {rows.map((r) => {
        const max = Math.max(r.today, r.plan ?? 0, r.limit ?? 0) * 1.1 || 1;
        const ok = (v: number) => (r.limit === null ? null : r.lower ? v <= r.limit : v >= r.limit);
        const pct = (v: number) => `${(100 * v) / max}%`;
        return (
          <div className="lb-row" role="row" key={r.label}>
            <span className="lb-label" role="rowheader">{r.label}</span>
            <div className="lb-track" role="cell" aria-hidden>
              <span className={`lb-bar today ${ok(r.today) === false ? "over" : ""}`} style={{ width: pct(r.today) }} />
              {r.plan !== null && <span className={`lb-bar plan ${ok(r.plan) === false ? "over" : ""}`} style={{ width: pct(r.plan) }} />}
              {r.limit !== null && <span className="lb-limit" style={{ left: pct(r.limit) }} title={`Your limit: ${r.limit} ${r.unit}`} />}
            </div>
            <span className="lb-vals num" role="cell">
              <span>Today {r.today.toPrecision(3)} {r.unit}{ok(r.today) === false ? " ✕" : ""}</span>
              {r.plan !== null && <span>Plan {r.plan.toPrecision(3)} {r.unit}{ok(r.plan) === false ? " ✕" : r.limit !== null ? " ✓" : ""}</span>}
            </span>
            <span className="lb-lim muted" role="cell">{r.limit === null ? "no limit" : `limit ${r.lower ? "≤" : "≥"} ${r.limit} ${r.unit}`}</span>
          </div>
        );
      })}
    </div>
  );
}

const FEAS_TEXT: Record<string, string> = {
  verified_feasible: "meets every requirement you set, confirmed by a measurement",
  predicted_feasible: "is predicted to meet every requirement you set",
  uncertain: "may or may not meet your requirements: the predicted range straddles a limit",
  infeasible: "is predicted to miss at least one requirement you set",
  verified_infeasible: "misses at least one requirement, confirmed by a measurement",
};

/** Step 1 of the decision: how the current setup performs, with its figures. Nothing here proposes a
 * change; the recommendation engine comes next and is reached from the bottom of the page. */
export default function SummaryPage({ res, spec, projectId, runId, version }: { res: OptimisationResult; spec: ProjectSpec; projectId: number; runId: number | null; version: number }) {
  const v = res.verdict;
  const b = res.baseline;
  const c = spec.constraints;
  const trace = useBaselineTrace(projectId, version, runId);
  const rows = [
    { label: "Average power", unit: "W", today: b.metrics.power_avg.value, plan: null, limit: c.max_power_w, lower: true },
    { label: "Latency per input", unit: "ms", today: b.metrics.latency.value, plan: null, limit: c.max_latency_ms, lower: true },
    { label: "Throughput", unit: "/s", today: b.metrics.capacity_fps.value, plan: null, limit: spec.workload.fps, lower: false },
    { label: "Temperature", unit: "°C", today: b.metrics.temperature_unthrottled.value, plan: null, limit: null, lower: true },
  ];
  return (
    <div className="summary-page">
      <section className="verdict-band" aria-labelledby="vb-h">
        <div className="vb-outcomes"><span>Your setup today</span> <Outcome state={b.feasibility} size="lg" /></div>
        <h2 id="vb-h" className="vb-sentence">
          {spec.name}: today it {FEAS_TEXT[b.feasibility] ?? b.feasibility.replace(/_/g, " ")}.
        </h2>
        {v?.baseline.problems.length ? (
          <ul className="vb-problems">{v.baseline.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        ) : null}
      </section>

      <section className="bottleneck" aria-labelledby="bn-h2">
        <h2 id="bn-h2">What limits it</h2>
        <p className="bn-main">{BOTTLENECK_PLAIN[b.bottleneck] ?? b.bottleneck}</p>
        <Why id={TERM_FOR[b.bottleneck] ?? "feasibility"} />
      </section>

      <section className="panel" aria-labelledby="lim-h">
        <h2 id="lim-h">Against your limits</h2>
        <p className="muted">Each bar is today's predicted value; the tick is the limit you set. A cross marks a missed requirement.</p>
        <LimitBars rows={rows} />
        <p className="hint">Temperature is shown without throttling: the level the chip would reach if it did not slow itself down. <Why id="throttling" /></p>
      </section>

      <h2 className="section-title">Key figures</h2>
      {trace.error && <p className="note" role="status">The figures are not available for this setup: {trace.error}.</p>}
      {!trace.data && !trace.error && <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w60" /></div>}
      {trace.data && (
        <>
          <TraceKpis t={trace.data.trace} />
          <KeyFigures projectId={projectId} a={trace.data} />
        </>
      )}

      <Level kind="engineering" title="Every number, with its range and source">
        <p className="muted">Settings: {configLabel(b.config)}. Engine {b.engine_version}.</p>
        <Checks r={b} />
        <MetricTable r={b} />
        <div className="actions">
          {runId && <button onClick={() => download(`/projects/${projectId}/runs/${runId}`, `analysis-${runId}.json`)}>Download the full analysis (JSON)</button>}
          <a className="btn-link" href={`#/p/${projectId}/system-map`}>Open the detailed engineering views</a>
        </div>
      </Level>

    </div>
  );
}
