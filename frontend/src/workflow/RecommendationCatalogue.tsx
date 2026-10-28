import { useCallback, useEffect, useRef, useState } from "react";
import AiBadge from "../components/AiBadge";
import { workflow } from "../api";
import { ErrorNote } from "../components/Help";
import { navigate } from "../router";
import type { AiCandidates, Rec, RecommendationSet } from "./types";

const STATUS: Record<Rec["status"], { label: string; cls: string }> = {
  recommended: { label: "Recommended (predicted)", cls: "st-rec" },
  option: { label: "Option, not evaluated yet", cls: "st-guide" },
  directional: { label: "Directional what-if", cls: "st-dir" },
  guidance: { label: "Engineering guidance", cls: "st-guide" },
  needs_input: { label: "Needs input", cls: "st-need" },
  ai_candidate: { label: "AI candidate, not evaluated", cls: "st-ai" },
};
const VALIDATION: Record<Rec["validation"]["state"], string> = {
  not_evaluated: "Not evaluated yet",
  evaluated: "Evaluated in a saved plan (predicted): meets the requirements",
  rejected: "Evaluated in a saved plan (predicted): does not meet the requirements",
  measured: "Measured on a real run of this setup",
};
const EFFECT_LABEL: Record<string, string> = {
  energy_saving_percent: "Energy per input, as part of the recommended setup",
  alone_energy_saving_percent: "Energy per input, this change alone",
  power_saving_percent: "Average power",
  temperature_change_c: "Temperature",
  energy_change_percent: "Energy per input",
  latency_change_percent: "Latency",
  power_change_percent: "Average power",
  temperature_change_percent: "Temperature",
};

function effect(k: string, v: number): string {
  if (k === "temperature_change_c") return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)} °C`;
  if (k.endsWith("_saving_percent")) return v >= 0 ? `−${v.toFixed(1)} %` : `+${Math.abs(v).toFixed(1)} %`;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)} %`;
}

function RecCard({ r, projectId, canWrite }: { r: Rec; projectId: number; canWrite: boolean }) {
  const st = STATUS[r.status];
  const exp = Object.entries(r.expected ?? {}).filter(([k]) => k in EFFECT_LABEL);
  return (
    <article className="rec-card" aria-labelledby={`rec-${r.id}`}>
      <header>
        <div>
          <span className="rec-area">{r.rank ? `#${r.rank} · ` : ""}{r.area_label}</span>
          <h4 id={`rec-${r.id}`}>{r.title}</h4>
        </div>
        <span className={`rec-status ${st.cls}`}>{st.label}</span>
      </header>
      <dl className="rec-kv">
        <dt>Problem</dt><dd>{r.problem || "–"}</dd>
        {r.change && <><dt>Change</dt><dd>{r.change}</dd></>}
        <dt>Why it may help</dt><dd>{r.why}</dd>
        <dt>Affects</dt><dd>{r.affects.join(", ") || "–"}</dd>
      </dl>
      {exp.length > 0 && (
        <table className="tbl rec-effects">
          <caption>Expected effect (engine prediction, not measured)</caption>
          <tbody>
            {exp.map(([k, v]) => <tr key={k}><th scope="row">{EFFECT_LABEL[k]}</th><td className="num">{effect(k, v)}</td></tr>)}
          </tbody>
        </table>
      )}
      <details>
        <summary>Trade-offs, requirements and evidence</summary>
        <dl className="rec-kv">
          <dt>Trade-offs</dt><dd>{r.tradeoffs.length ? <ul>{r.tradeoffs.map((t) => <li key={t}>{t}</li>)}</ul> : "None stated"}</dd>
          <dt>Needs</dt><dd>{r.requires.length ? <ul>{r.requires.map((t) => <li key={t}>{t}</li>)}</ul> : "–"}</dd>
          {r.evidence && <><dt>Evidence</dt><dd>{r.evidence}</dd></>}
          {r.confidence && <><dt>Confidence</dt><dd>{r.confidence}</dd></>}
          {!!r.assumptions?.length && <><dt>Assumptions</dt><dd>{r.assumptions.join(" ")}</dd></>}
          {!!r.notes?.length && <><dt>Checks on the AI reply</dt><dd>{r.notes.join("; ")}</dd></>}
        </dl>
      </details>
      <footer>
        <span className={`rec-val val-${r.validation.state}`}>
          {VALIDATION[r.validation.state]}
          {r.validation.plan_name ? ` (plan “${r.validation.plan_name}” v${r.validation.plan_version}${r.validation.stale ? ", from an older analysis" : ""})` : ""}
        </span>
        {r.plan_change_id ? (
          <button disabled={!canWrite} onClick={() => navigate(`/p/${projectId}/recommendations?tab=plan&add=${encodeURIComponent(r.plan_change_id!)}`)}>
            Evaluate in the plan builder
          </button>
        ) : (
          <span className="hint">{r.status === "needs_input" ? "Add the input above to unlock this." : "The engine cannot evaluate this one; review it by hand."}</span>
        )}
      </footer>
    </article>
  );
}

function AiSection({ projectId, canWrite, initial }: { projectId: number; canWrite: boolean; initial: AiCandidates | null }) {
  const [ai, setAi] = useState<AiCandidates | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const here = useRef<HTMLElement>(null);
  useEffect(() => {
    // The recommendation engine links here with focus=ai.
    if (window.location.hash.includes("focus=ai")) here.current?.scrollIntoView({ block: "start" });
  }, []);
  async function ask(refresh: boolean) {
    setBusy(true);
    setError(null);
    try {
      setAi(await workflow.aiCandidates(projectId, refresh));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section ref={here} className="panel" aria-labelledby="ai-h">
      <div className="row">
        <h3 id="ai-h">AI candidates (optional)</h3>
        <button disabled={busy || !canWrite} onClick={() => ask(!!ai)}>
          {busy ? "Asking the model…" : ai ? "Ask again" : "Ask the AI for candidates"}
        </button>
      </div>
      <AiBadge role="proposes extra changes, labelled not evaluated" />
      <p className="hint">
        Sends the engine's summary for this analysis (model and device names, today's settings and metrics, failing requirements,
        your requirements and the ids of changes the engine can evaluate) to the configured provider, Nebius Token Factory by default.
        No file contents are sent. Replies are checked against a schema, figures not in the analysis are removed, and nothing is applied.
      </p>
      <ErrorNote error={error} />
      {ai && (
        <>
          <p className="muted">{ai.label} Model: {ai.model}{ai.cached ? " (cached for this analysis)" : ""}.</p>
          {ai.summary && <p>{ai.summary}</p>}
          <div className="rec-grid">
            {ai.candidates.map((r) => <RecCard key={r.id} r={r} projectId={projectId} canWrite={canWrite} />)}
          </div>
          {ai.candidates.length === 0 && <p className="hint">The model returned no usable candidates.</p>}
          {ai.dropped.length > 0 && (
            <p className="hint">Rejected by the checks: {ai.dropped.map((d) => `${d.title} (${d.reason})`).join("; ")}.</p>
          )}
        </>
      )}
    </section>
  );
}

/** Every recommendation from the latest analysis, split into software and hardware, ranked by the
 * project's requirements and priority, each with its status and validation state. */
export default function RecommendationCatalogue({ projectId, canWrite, refreshKey }: { projectId: number; canWrite: boolean; refreshKey: string }) {
  const [set, setSet] = useState<RecommendationSet | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => {
    setError(null);
    workflow.recommendations(projectId).then(setSet).catch(setError);
  }, [projectId]);
  useEffect(() => {
    load();
  }, [load, refreshKey]);
  if (error) return <ErrorNote error={error} onRetry={load} />;
  if (!set) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w90" /></div>;
  if (!set.analysed) return <section className="panel empty-state"><p>{set.message}</p></section>;
  return (
    <>
      {set.stale && <p className="note-warn" role="status">The inputs changed after this analysis. These recommendations are kept, but run the analysis again before acting on them.</p>}
      <section className="panel" aria-labelledby="cat-h">
        <h2 id="cat-h">Recommendations by category</h2>
        <p className="muted">
          From analysis #{set.source_run_id}. Today: {set.baseline?.feasibility.replace(/_/g, " ")}, limited by {set.baseline?.bottleneck}
          {set.baseline?.failing.length ? `; fails ${set.baseline.failing.join(", ")}` : ""}. Ranked by: fixes a failing requirement first, then your priority ({set.priority?.replace(/_/g, " ")}).
        </p>
        <details className="hint">
          <summary>What the statuses mean</summary>
          <ul>{Object.entries(set.statuses ?? {}).map(([k, v]) => <li key={k}><strong>{STATUS[k as Rec["status"]]?.label ?? k}:</strong> {v}</li>)}</ul>
        </details>
      </section>
      {(["software", "hardware"] as const).map((cat) => (
        <section key={cat} className="panel" aria-labelledby={`cat-${cat}`}>
          <h3 id={`cat-${cat}`}>{cat === "software" ? "Software" : "Hardware"} ({set[cat].length})</h3>
          <div className="rec-grid">
            {set[cat].map((r) => <RecCard key={r.id} r={r} projectId={projectId} canWrite={canWrite} />)}
          </div>
        </section>
      ))}
      <AiSection projectId={projectId} canWrite={canWrite} initial={set.ai} />
    </>
  );
}
