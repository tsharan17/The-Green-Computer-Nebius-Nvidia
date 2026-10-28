import { SIDE_VIEWS, STAGES, STEPS, type SideView, type Step, hrefOf, neighbours } from "../flow";
import { navigate } from "../router";

/** "Step 4 of 6: Recommendations", what to do here, and the way back from a side view. */
export function StepHeader({ projectId, step, side }: { projectId: number; step: Step; side: SideView | null }) {
  return (
    <div className="step-head">
      <p className="step-kicker">Step {step.stage} of {STAGES.length}: {STAGES[step.stage - 1]}</p>
      {side ? (
        <p className="step-todo">
          <strong>{side.title}</strong> is an optional detail view of <em>{step.title}</em>.{" "}
          <a href={hrefOf(projectId, step)}>Back to {step.title}</a>
        </p>
      ) : (
        <p className="step-todo"><strong>{step.title}{step.optional ? " (optional)" : ""}.</strong> {step.todo}</p>
      )}
    </div>
  );
}

/** The steps of the current stage, numbered in order, then its optional views under "More". */
export function SubSteps({ projectId, step, side }: { projectId: number; step: Step; side: SideView | null }) {
  const steps = STEPS.filter((s) => s.stage === step.stage);
  const ids = new Set(steps.map((s) => s.id));
  const views = SIDE_VIEWS.filter((v) => ids.has(v.parent) && v.tab); // views with their own pages are in the side navigation
  if (steps.length < 2 && !views.length) return null;
  return (
    <div className="substeps">
      {steps.length > 1 && (
        <div className="tabs" role="tablist" aria-label={`Steps in ${STAGES[step.stage - 1]}`}>
          {steps.map((s, i) => {
            const on = s.id === step.id && !side;
            return (
              <button key={s.id} role="tab" aria-selected={on} className={`tab ${on ? "on" : ""}`} onClick={() => navigate(hrefOf(projectId, s))}>
                <span className="substep-n" aria-hidden>{step.stage}.{i + 1}</span> {s.title}{s.optional ? " (optional)" : ""}
              </button>
            );
          })}
        </div>
      )}
      {views.length > 0 && (
        <div className="more-views" role="tablist" aria-label="Optional detail views">
          <span className="muted">More detail (optional):</span>
          {views.map((v) => (
            <button key={v.id} role="tab" aria-selected={side?.id === v.id} className={`chip-link ${side?.id === v.id ? "on" : ""}`} onClick={() => navigate(hrefOf(projectId, v))}>
              {v.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Back and Next along the one path. `nextAction` replaces the Next link where the next step needs
 * something to happen first (running the analysis). */
export function StepFooter({
  projectId,
  step,
  side = null,
  nextAction,
}: {
  projectId: number;
  step: Step;
  /** On an optional view, Back returns to the step it belongs to. */
  side?: SideView | null;
  nextAction?: { label: string; run: () => void; disabled?: boolean };
}) {
  const { back, next } = neighbours(step);
  return (
    <nav className="step-foot" aria-label="Back and next">
      {side ? (
        <a className="btn-link" href={hrefOf(projectId, step)}>← Back to {step.title}</a>
      ) : back ? (
        <a className="btn-link" href={hrefOf(projectId, back)}>← Back: {back.title}</a>
      ) : <span />}
      {nextAction ? (
        <button className="primary big" disabled={nextAction.disabled} onClick={nextAction.run}>{nextAction.label} →</button>
      ) : next ? (
        <a className="btn-link primary big" href={hrefOf(projectId, next)}>
          Next: {next.title}{next.optional ? " (optional)" : ""} →
        </a>
      ) : (
        <span className="muted">This is the last step.</span>
      )}
    </nav>
  );
}
