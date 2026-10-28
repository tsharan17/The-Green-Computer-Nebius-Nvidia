import TraceFigure from "../components/TraceFigure";
import { Figure, bytes, ms } from "./chart";
import { TraceBar, useTrace } from "./TraceBar";

export default function MemoryPage({ projectId, version, hasBest }: { projectId: number; version: number; hasBest: boolean }) {
  const state = useTrace(projectId, version, hasBest);
  const t = state.data?.trace;
  if (!t) return <div className="twin-page"><TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="memory" /></div>;
  const tid = state.data!.id;
  const tot = t.totals;
  const fits = tot.peak_working_memory_bytes <= tot.memory_capacity_bytes;
  return (
    <div className="twin-page">
      <TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="memory" />
      <div className="kpis">
        <div><span>Peak working memory</span><strong className="num">{bytes(tot.peak_working_memory_bytes)}</strong><small>{fits ? "fits" : "exceeds"} {bytes(tot.memory_capacity_bytes)} usable</small></div>
        <div><span>Weights</span><strong className="num">{bytes(tot.weights_bytes)}</strong><small>{t.components.some((c) => c.id === "flash") ? "kept in flash" : "resident in memory"}</small></div>
        <div><span>Fit check used</span><strong className="num">{bytes(tot.analysis_memory_bytes)}</strong><small>the engine's conservative estimate</small></div>
      </div>
      {!fits && <p className="error" role="alert">At its peak this setup needs more memory than the device can use. See the recommendations for smaller precisions or batches.</p>}
      <Figure
        title="Memory over the batch"
        note={t.fidelity === "graph" ? "Activations follow tensor lifetimes from the graph: allocated by their producer, freed after their last reader." : t.fidelity_note}
        table={
          <table className="tbl">
            <thead><tr><th>Operator</th><th className="num">Time</th><th className="num">Weights</th><th className="num">Runtime</th><th className="num">Activations</th><th className="num">Total</th></tr></thead>
            <tbody>{t.memory_timeline.map((p) => (
              <tr key={p.event}><td>{t.events[p.event]?.name}</td><td className="num">{ms(p.t_ms)}</td><td className="num">{bytes(p.weights_bytes)}</td><td className="num">{bytes(p.runtime_bytes)}</td><td className="num">{bytes(p.activation_bytes)}</td><td className="num">{bytes(p.total_bytes)}</td></tr>
            ))}</tbody>
          </table>
        }
      >
        <TraceFigure projectId={projectId} traceId={tid} name="memory-timeline" alt={`Memory in use over one batch, peaking at ${bytes(tot.peak_working_memory_bytes)}`} />
      </Figure>
      {t.tensors.length > 0 && (
        <Figure
          title="Tensor lifetimes"
          note="The largest tensors, from the operator that writes them to the last one that reads them, shaded by size."
          table={<table className="tbl"><thead><tr><th>Tensor</th><th>Kind</th><th className="num">Size</th><th className="num">From operator</th><th className="num">To operator</th></tr></thead><tbody>{[...t.tensors].sort((a, b) => b.bytes - a.bytes).slice(0, 40).map((s) => <tr key={s.id}><td>{s.id}</td><td>{s.kind}</td><td className="num">{bytes(s.bytes)}</td><td className="num">{s.producer < 0 ? "start" : s.producer}</td><td className="num">{s.last_use}</td></tr>)}</tbody></table>}
        >
          <TraceFigure projectId={projectId} traceId={tid} name="tensor-lifetimes" alt="Heat map of when each large tensor is alive during execution" />
        </Figure>
      )}
      <Figure
        title="Throughput and bandwidth pressure"
        note="How hard each operator drives the memory path and the compute engine. Operators near 1 on memory bandwidth are memory-bound."
        table={<table className="tbl"><thead><tr><th>Operator</th><th>Limited by</th><th className="num">Bandwidth used</th><th className="num">Power</th></tr></thead><tbody>{t.events.filter((e) => e.kind !== "idle").map((e) => <tr key={e.index}><td>{e.name}</td><td>{e.bound}</td><td className="num">{(e.bandwidth_utilisation * 100).toFixed(0)}%</td><td className="num">{e.power_w.toPrecision(3)} W</td></tr>)}</tbody></table>}
      >
        <TraceFigure projectId={projectId} traceId={tid} name="throughput-heatmap" alt="Heat map of memory bandwidth, compute, overhead and power for each operator" />
      </Figure>
    </div>
  );
}