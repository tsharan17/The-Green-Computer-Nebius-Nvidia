import { DesignIdeas } from "../components/results/OtherOptions";
import Recommendations, { CheapestFix } from "../components/results/Recommendations";
import VerdictBanner from "../components/results/VerdictBanner";
import { SOURCE_WORDS } from "../lib/plain";
import type { OptimisationResult, ProjectSpec } from "../types";

function HowSure({ res, go }: { res: OptimisationResult; go: (p: "verify" | "export" | "explore") => void }) {
  const r = res.best ?? res.baseline;
  const counts = new Map<string, number>();
  Object.values(r.metrics).forEach((m) => counts.set(m.provenance, (counts.get(m.provenance) ?? 0) + 1));
  const measured = counts.get("measured") ?? 0;
  const calibrated = counts.get("calibrated") ?? 0;
  return (
    <section className="panel how-sure">
      <h2>How sure are we?</h2>
      <p>
        {measured + calibrated > 0
          ? "Some of these numbers are backed by real measurements."
          : "Everything above is a prediction from our physics model. Nothing has been measured on your hardware yet."}{" "}
        Every number comes with a best-case to worst-case range, and your requirements are never relaxed to make a result
        look good.
      </p>
      <ul className="source-list">
        {[...counts.entries()].map(([k, n]) => (
          <li key={k}>
            <span className={`badge badge-${k}`}>{SOURCE_WORDS[k as keyof typeof SOURCE_WORDS] ?? k}</span> {n} figure{n === 1 ? "" : "s"}
          </li>
        ))}
      </ul>
      <div className="cta-row">
        <button className="primary" onClick={() => go("verify")}>
          Open the run, history and export tools
        </button>
        <button onClick={() => go("export")}>Get the report and recipe</button>
        <button className="link" onClick={() => go("explore")}>
          Show the technical details
        </button>
      </div>
    </section>
  );
}

export default function ResultsPage({
  res,
  spec,
  go,
}: {
  res: OptimisationResult;
  spec: ProjectSpec;
  go: (p: "start" | "verify" | "export" | "explore") => void;
}) {
  return (
    <div className="results-page">
      <section className="report-head">
        <div>
          <span className="eyebrow">Analysis</span>
          <h2>{spec.name}</h2>
          <p className="muted">
            {spec.workload.fps} frames/s · {spec.workload.ambient_c} °C room · {spec.constraints.max_power_w} W limit · ≥ {Math.round(spec.constraints.min_accuracy * 100)}% accuracy
          </p>
        </div>
        <span className="muted">engine {res.baseline.engine_version}</span>
      </section>
      {res.verdict && <VerdictBanner verdict={res.verdict} wl={spec.workload} cons={spec.constraints} />}
      {res.repair && <CheapestFix repair={res.repair} />}
      <Recommendations advice={res.advice} />
      <DesignIdeas whatifs={res.whatifs} limiting={res.limiting_factor} />
      <HowSure res={res} go={go} />
      <p className="hint foot-note">
        Searched {res.evaluated.toLocaleString()} settings in {res.search_seconds.toFixed(1)} s ·{" "}
        <button className="link" onClick={() => go("start")}>
          change your inputs
        </button>
      </p>
    </div>
  );
}
