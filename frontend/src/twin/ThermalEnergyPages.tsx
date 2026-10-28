import { EvidencePanel, RuntimeAndMemory, ThermalNodes } from "./PhysicsSections";
import TraceFigure from "../components/TraceFigure";
import ExplorePage from "../pages/ExplorePage";
import type { OptimisationResult, ProjectSpec } from "../types";
import { ENERGY_KEYS, ENERGY_LABEL, Figure, ms, num } from "./chart";
import { TraceBar, useTrace } from "./TraceBar";

function fmtTime(s: number) {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${(s / 60).toFixed(s < 600 ? 1 : 0)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

const PLAN_NOTE = <p className="hint">The detailed physics views below describe the current and the recommended setup; pick one of them above to see them.</p>;

export function ThermalPage({ projectId, version, hasBest }: { projectId: number; version: number; hasBest: boolean }) {
  const state = useTrace(projectId, version, hasBest);
  const t = state.data?.trace;
  if (!t) return <div className="twin-page"><TraceBar projectId={projectId} state={state} hasBest={hasBest} /></div>;
  const th = t.thermal;
  const tid = state.data!.id;
  const heat = t.components.filter((c) => c.heat_w > 0).sort((a, b) => b.heat_w - a.heat_w);
  return (
    <div className="twin-page">
      <TraceBar projectId={projectId} state={state} hasBest={hasBest} />
      <div className="kpis">
        <div><span>Settles at</span><strong className="num">{num(th.steady_c)} °C</strong><small>{th.throttled ? `held there by throttling (clock ${(th.clock_factor * 100).toFixed(0)}%)` : `${num(th.throttle_c - th.steady_c)} °C below throttling`}</small></div>
        <div><span>Without throttling it would reach</span><strong className="num">{num(th.unthrottled_c)} °C</strong><small>shutdown at {num(th.shutdown_c)} °C</small></div>
        <div><span>Cooling fitted</span><strong className="num">{num(th.r_th_k_per_w)} K/W</strong><small>{th.cooling_name}</small></div>
        <div><span>Cooling needed</span><strong className="num">{th.r_th_needed_k_per_w ? `≤ ${num(th.r_th_needed_k_per_w)} K/W` : "–"}</strong><small>to stay below the throttle point</small></div>
      </div>
      <Figure title="Board temperature map" note="Where the heat sits once the board has warmed up. Left: the board, hotter parts in deeper red; the dashed box is the cooler on the chip. Right: the hottest point of each part against the temperature where the chip slows itself down. The chip temperature is the engine's number; the spread around it is illustrative, not a CFD solve.">
        <TraceFigure projectId={projectId} traceId={tid} name="thermal-board" alt={`Predicted steady-state temperature field of ${t.device_name}, peaking at ${num(th.steady_c)} °C`} />
      </Figure>
      <Figure
        title="Temperature under sustained load"
        note="How the chip heats up after it starts working at this workload, and whether it reaches the point where it slows down (lumped thermal model)."
        table={<table className="tbl"><thead><tr><th className="num">Time</th><th className="num">°C</th></tr></thead><tbody>{th.warmup.map(([a, b]) => <tr key={a}><td className="num">{fmtTime(a)}</td><td className="num">{num(b)}</td></tr>)}</tbody></table>}
      >
        <TraceFigure projectId={projectId} traceId={tid} name="thermal-warmup" alt={`Predicted temperature over time, settling at ${num(th.steady_c)} °C against a throttle point of ${num(th.throttle_c)} °C`} />
      </Figure>
      <Figure
        title="Where the heat comes from"
        note={th.note}
        table={<table className="tbl"><tbody>{heat.map((c) => <tr key={c.id}><td>{c.name}</td><td className="num">{num(c.heat_w)} W</td></tr>)}</tbody></table>}
      >
        <TraceFigure projectId={projectId} traceId={tid} name="heat-sources" alt="Heat dissipated by each component of the hardware, in watts" />
      </Figure>
      {state.which === "plan" ? PLAN_NOTE : (
        <>
          <ThermalNodes projectId={projectId} which={state.which} version={version} />
        </>
      )}
    </div>
  );
}

export function EnergyPage({
  projectId,
  version,
  hasBest,
  res,
  spec,
}: {
  projectId: number;
  version: number;
  hasBest: boolean;
  res: OptimisationResult | null;
  spec: ProjectSpec;
}) {
  const state = useTrace(projectId, version, hasBest);
  const t = state.data?.trace;
  const tid = state.data?.id;
  const r = res ? (state.which === "best" && res.best ? res.best : res.baseline) : null;
  return (
    <div className="twin-page">
      <TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="events" />
      {t && tid !== undefined && (
        <>
          <div className="kpis">
            <div><span>Energy per input</span><strong className="num">{num(t.totals.energy_per_inference_mj)} mJ</strong><small>including idle time between inputs</small></div>
            <div><span>Average power</span><strong className="num">{num(t.totals.average_power_w)} W</strong><small>{num(t.totals.active_power_w)} W while computing</small></div>
            {r && state.which !== "plan" && <div><span>Electricity</span><strong className="num">{num(r.metrics.cost_per_day.value)} / day</strong><small>at {spec.workload.price_per_kwh} per kWh, {spec.workload.hours_per_day} h/day</small></div>}
            {r && state.which !== "plan" && <div><span>Emissions</span><strong className="num">{num(r.metrics.carbon_per_day.value)} kg CO₂e / day</strong><small>at {spec.workload.grid_gco2_per_kwh} g/kWh</small></div>}
          </div>
          <Figure
            title="Energy per input, by cause"
            note="Each operator's energy split by the power model's terms, summed over one batch period and divided by the batch size."
            table={<table className="tbl"><tbody>{ENERGY_KEYS.map((k) => <tr key={k}><td>{ENERGY_LABEL[k]}</td><td className="num">{num(t.energy_per_inference[k])} mJ</td></tr>)}</tbody></table>}
          >
            <TraceFigure projectId={projectId} traceId={tid} name="energy-split" alt={`Energy per input split by cause, ${num(t.totals.energy_per_inference_mj)} mJ in total`} />
          </Figure>
          <Figure
            title="Energy map"
            note="Which stage of the model spends which kind of energy during one batch."
            table={<table className="tbl"><thead><tr><th>Stage</th><th className="num">Time</th><th className="num">Energy</th></tr></thead><tbody>{t.stages.map((s) => <tr key={s.index}><td>{s.index + 1}. {s.dominant_op}</td><td className="num">{ms(s.duration_ms)}</td><td className="num">{num(s.energy_mj)} mJ</td></tr>)}</tbody></table>}
          >
            <TraceFigure projectId={projectId} traceId={tid} name="energy-stage-heatmap" alt="Heat map of energy per stage and cause" />
          </Figure>
          <Figure title="Power over the batch" note="Power drawn by each operator across one batch, with the average marked.">
            <TraceFigure projectId={projectId} traceId={tid} name="power-timeline" alt="Power in watts over one batch" />
          </Figure>
        </>
      )}
      {state.which === "plan" ? PLAN_NOTE : (
        <>
          <RuntimeAndMemory projectId={projectId} which={state.which} version={version} />
          <EvidencePanel projectId={projectId} version={version} />
        </>
      )}
      {res ? (
        <section className="explore-embed">
          <h2 className="section-title">Every metric, with its range and source</h2>
          <ExplorePage res={res} spec={spec} projectId={projectId} />
        </section>
      ) : (
        <p className="hint">Run the analysis to see every metric with its best-to-worst range, the options the search tried and the co-design comparison.</p>
      )}
    </div>
  );
}