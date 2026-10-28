import { useState } from "react";
import CodesignTable from "../components/CodesignTable";
import LayerView from "../components/LayerView";
import ParetoChart from "../components/ParetoChart";
import ThermalChart from "../components/ThermalChart";
import { Checks, Chip, MetricTable, configLabel, fmt } from "../components/common";
import type { EvaluationResult, OptimisationResult, ProjectSpec } from "../types";

type Sub = "numbers" | "layers" | "heat" | "options";

function Block({ r, title }: { r: EvaluationResult; title: string }) {
  return (
    <section className="panel">
      <div className="row">
        <h2>{title}</h2>
        <Chip state={r.feasibility} />
      </div>
      <p className="hint">
        {configLabel(r.config)} · limited by: <strong>{r.bottleneck}</strong> · throttling risk: <strong>{r.throttle_risk}</strong>
      </p>
      <Checks r={r} />
      <MetricTable r={r} />
      {r.notes.map((n) => (
        <p key={n} className="note">{n}</p>
      ))}
    </section>
  );
}

export default function ExplorePage({ res, spec, projectId }: { res: OptimisationResult; spec: ProjectSpec; projectId: number }) {
  const [sub, setSub] = useState<Sub>("numbers");
  const [which, setWhich] = useState<"baseline" | "best">(res.best ? "best" : "baseline");
  const r = which === "best" && res.best ? res.best : res.baseline;
  return (
    <div className="explore">
      <p className="hint">
        The full technical picture behind the results: every number with its best-case to worst-case range and where it
        came from, the heat model, and all the options the search tried.
      </p>
      <nav className="tabs" role="tablist">
        {(
          [
            ["numbers", "Numbers and checks"],
            ["layers", "Layer by layer"],
            ["heat", "Heat and power"],
            ["options", "All options tried"],
          ] as [Sub, string][]
        ).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={sub === k} className={sub === k ? "tab on" : "tab"} onClick={() => setSub(k)}>
            {label}
          </button>
        ))}
      </nav>

      {sub !== "options" && res.best && (
        <div className="seg" style={{ marginBottom: 12 }}>
          <button className={which === "baseline" ? "on" : ""} onClick={() => setWhich("baseline")}>Current setup</button>
          <button className={which === "best" ? "on" : ""} onClick={() => setWhich("best")}>Recommended</button>
        </div>
      )}

      {sub === "numbers" && <Block r={r} title={which === "best" ? "Recommended setup" : "Current setup"} />}

      {sub === "layers" && <LayerView projectId={projectId} config={r.config} title={which === "best" ? "recommended setup" : "current setup"} />}

      {sub === "heat" && (
        <section className="panel">
          <h2>Heat and power over time</h2>
          <p className="hint">{configLabel(r.config)} · room {fmt(spec.workload.ambient_c)} °C</p>
          <ThermalChart r={r} ambientC={spec.workload.ambient_c} />
          <div className="facts">
            <div><span>Without slowing down</span><strong>{fmt(r.metrics.temperature_unthrottled.value)} °C</strong></div>
            <div><span>After slowing down</span><strong>{fmt(r.metrics.temperature.value)} °C</strong></div>
            <div><span>Speed kept</span><strong>{(r.solver.clock_factor * 100).toFixed(0)}%</strong></div>
            <div><span>Capacity</span><strong>{fmt(r.metrics.capacity_fps.value)} frames/s</strong></div>
          </div>
          {r.notes.map((n) => (
            <p key={n} className="note">{n}</p>
          ))}
        </section>
      )}

      {sub === "options" && (
        <>
          <section className="panel">
            <h2>Software only, hardware only, or both</h2>
            <CodesignTable cd={res.codesign} />
          </section>
          <section className="panel">
            <h2>Every option the search tried</h2>
            <ParetoChart points={res.points} baseline={res.baseline} />
            <p className="hint">
              {res.evaluated.toLocaleString()} settings: {Object.entries(res.counts).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ")}.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
