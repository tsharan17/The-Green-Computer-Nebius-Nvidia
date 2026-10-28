import type { HardwareProfile, ModelProfile, OptimisationResult, ProjectOut } from "../types";
import { BOTTLENECK_PLAIN } from "../lib/glossary";
import { Outcome } from "./Help";

/** The project's decision at a glance, on every project screen: where it stands, what to do.
 * Until the user reaches the recommendation engine (`decide`), it describes today's setup only. */
export default function DecisionBar({
  decide,
  project,
  res,
  stale,
  busy,
  canWrite,
  model,
  device,
  onAnalyse,
  next,
}: {
  decide: boolean;
  project: ProjectOut;
  res: OptimisationResult | null;
  stale: boolean;
  busy: boolean;
  canWrite: boolean;
  model?: ModelProfile;
  device?: HardwareProfile;
  onAnalyse: () => void;
  /** The next step along the path, from flow.ts. */
  next: { label: string; href: string } | null;
}) {
  const s = project.spec;
  const v = res?.verdict;
  const rec = res?.advice.filter((a) => a.in_recommended) ?? [];
  const conf = rec.some((a) => a.confidence === "low") ? "Low" : rec.some((a) => a.confidence === "medium") ? "Medium" : rec.length ? "High" : "–";
  const saving = v?.savings?.energy_percent;

  // One button, and it always agrees with the page's own Next button: analyse first, then move on.
  let action: { label: string; href?: string; run?: boolean } | null;
  if (!res || stale) action = { label: busy ? "Analysing…" : stale ? "Analyse the new inputs" : "Analyse", run: true };
  else action = next;

  return (
    <section className="decision-bar" aria-label="Decision summary">
      <div className="db-cell">
        <span className="db-label">Current setup</span>
        <strong>{model?.name ?? s.model_id}</strong>
        <span className="db-sub">on {device?.name ?? s.device_id}, {s.workload.fps} inputs/s</span>
      </div>
      <div className="db-cell">
        <span className="db-label">Outcome today</span>
        <Outcome state={res?.baseline.feasibility} />
        {stale && <span className="db-sub warn-text">Inputs changed since this analysis</span>}
      </div>
      {!decide && (
        <div className="db-cell">
          <span className="db-label">What limits it</span>
          <span className="db-sub">{res ? BOTTLENECK_PLAIN[res.baseline.bottleneck] ?? res.baseline.bottleneck : "Analyse first"}</span>
        </div>
      )}
      {decide && (<>
      <div className="db-cell">
        <span className="db-label">Recommended plan</span>
        {res?.best ? (
          <>
            <strong>{rec.length || "No"} change{rec.length === 1 ? "" : "s"}</strong>
            <span className="db-sub"><Outcome state={res.best.feasibility} /></span>
          </>
        ) : (
          <span className="db-sub">{res ? "No setup meets every requirement" : "Analyse first"}</span>
        )}
      </div>
      <div className="db-cell">
        <span className="db-label">Expected improvement</span>
        <strong className="num">{saving !== undefined && saving !== null ? `${saving >= 0 ? "−" : "+"}${Math.abs(saving).toFixed(0)}% energy` : "–"}</strong>
        {v?.savings && <span className="db-sub num">{v.savings.power_percent.toFixed(0)}% less power</span>}
      </div>
      <div className="db-cell">
        <span className="db-label">Confidence</span>
        <strong>{conf}</strong>
        <span className="db-sub">{res ? "predicted, not measured" : ""}</span>
      </div>
      </>)}
      {action && (
        <div className="db-action">
          {action.run ? (
            <button className="primary" disabled={busy || !canWrite} onClick={onAnalyse}>{action.label}</button>
          ) : (
            <a className="btn-link primary" href={action.href}>{action.label} →</a>
          )}
        </div>
      )}
    </section>
  );
}
