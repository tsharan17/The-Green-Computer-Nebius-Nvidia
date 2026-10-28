import { useCallback, useEffect, useRef, useState } from "react";
import { type ExecutionJob, execution, twin } from "../api";
import { when } from "../pages/WorkspacePage";
import { Figure, bytes, ms, num } from "../twin/chart";
import type { TraceEvent } from "../twin/types";

interface Backend { id: string; name: string; location: string; status: string; detail: string; enable: string; measures: string[] }

const STATUS: Record<string, string> = {
  available: "chip-predicted_feasible", disabled: "chip-uncertain", needs_credential: "chip-uncertain",
  unavailable: "chip-infeasible", planned: "",
};

export default function ExecutionPanel({ projectId, canWrite, onMeasured }: { projectId: number; canWrite: boolean; onMeasured: () => void }) {
  const [backends, setBackends] = useState<Backend[]>([]);
  const [backend, setBackend] = useState("local-cpu");
  const [batch, setBatch] = useState(1);
  const [runs, setRuns] = useState(30);
  const [jobs, setJobs] = useState<ExecutionJob[]>([]);
  const [current, setCurrent] = useState<ExecutionJob | null>(null);
  const [ops, setOps] = useState<TraceEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    const [b, j] = await Promise.all([execution.backends() as unknown as Promise<Backend[]>, execution.list(projectId)]);
    setBackends(b);
    setJobs(j);
    setCurrent((c) => c ?? j[0] ?? null);
  }, [projectId]);
  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [load]);

  useEffect(() => {
    if (!current?.trace_id) return setOps([]);
    twin.get(projectId, current.trace_id).then((t) => setOps([...t.trace.events].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 15))).catch(() => setOps([]));
  }, [current?.trace_id, projectId]);

  function poll(id: number) {
    timer.current = window.setTimeout(async () => {
      try {
        const j = await execution.get(projectId, id);
        setCurrent(j);
        if (j.status === "queued" || j.status === "running") poll(id);
        else {
          setJobs(await execution.list(projectId));
          if (j.status === "succeeded") onMeasured();
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 800);
  }

  async function start() {
    setError(null);
    try {
      const j = await execution.run(projectId, { backend, batch, runs, warmup: Math.min(5, runs) });
      setCurrent(j);
      poll(j.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const local = backends.filter((b) => b.location === "local");
  const sel = local.find((b) => b.id === backend);
  const busy = current?.status === "queued" || current?.status === "running";
  const r = current?.result;
  const cmp = r?.comparison;

  return (
    <div className="execution">
      <section className="panel" aria-labelledby="run-h">
        <h2 id="run-h">Run the model on this server</h2>
        <p className="hint">
          Runs the project's ONNX file (or the reference architecture of ResNet-50 / MobileNetV2) with ONNX Runtime on synthetic inputs, in a
          separate time-limited process. It measures latency and per-operator time on <strong>this server's hardware</strong>, not on the
          project's target device; power and temperature are not measured on this path.
        </p>
        <div className="inline-form">
          <label className="field">Where
            <select value={backend} onChange={(e) => setBackend(e.target.value)}>
              {local.map((b) => <option key={b.id} value={b.id} disabled={b.status !== "available"}>{b.name}{b.status !== "available" ? ` (${b.status.replace("_", " ")})` : ""}</option>)}
            </select>
          </label>
          <label className="field">Batch<input type="number" min={1} max={64} value={batch} onChange={(e) => setBatch(Math.max(1, Math.min(64, Number(e.target.value) || 1)))} /></label>
          <label className="field">Timed runs<input type="number" min={3} max={500} value={runs} onChange={(e) => setRuns(Math.max(3, Math.min(500, Number(e.target.value) || 30)))} /></label>
          <button className="primary" disabled={!canWrite || busy || sel?.status !== "available"} onClick={start}>{busy ? "Running…" : "Run now"}</button>
        </div>
        {sel && sel.status !== "available" && <p className="note">{sel.detail} {sel.enable}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <details>
          <summary>All execution backends</summary>
          <ul className="cap-list">
            {backends.map((b) => (
              <li key={b.id}>
                <div className="row"><strong>{b.name}</strong><span className={`chip ${STATUS[b.status] ?? ""}`}>{b.status.replace("_", " ")}</span></div>
                <p>{b.detail}</p>
                {b.enable && b.status !== "available" && <p className="hint">To enable: {b.enable}</p>}
              </li>
            ))}
          </ul>
        </details>
      </section>

      {current && (
        <section className="panel" aria-live="polite">
          <div className="row">
            <h2>Run {current.id}</h2>
            <span className={`chip ${current.status === "succeeded" ? "chip-predicted_feasible" : current.status === "failed" ? "chip-infeasible" : "chip-uncertain"}`}>{current.status}</span>
          </div>
          {current.error && <p className="error">{current.error}</p>}
          {busy && <p className="muted">Measuring… this usually takes a few seconds to a minute.</p>}
          {r?.latency_ms && (
            <>
              <p className="muted">{r.hardware_label}, ONNX Runtime {r.onnxruntime}, {r.runs} runs, batch {String(current.params.batch)}, inputs {JSON.stringify(r.inputs)}</p>
              <div className="kpis">
                <div><span>Mean latency (measured)</span><strong className="num">{ms(r.latency_ms.mean)}</strong><small>p50 {ms(r.latency_ms.p50)}, p95 {ms(r.latency_ms.p95)}</small></div>
                <div><span>Throughput (measured)</span><strong className="num">{num(r.throughput_fps ?? 0)} /s</strong><small>{r.precision} on {r.providers?.join(", ")}</small></div>
                <div><span>Peak process memory</span><strong className="num">{r.peak_rss_bytes ? bytes(r.peak_rss_bytes) : "not available"}</strong><small>whole worker process</small></div>
                {cmp?.predicted_batch_ms !== undefined && (
                  <div><span>Prediction for this CPU</span><strong className="num">{ms(cmp.predicted_batch_ms)}</strong>
                    <small>{cmp.error_percent !== null && cmp.error_percent !== undefined ? `${cmp.error_percent > 0 ? "+" : ""}${cmp.error_percent.toFixed(0)}% vs measured` : ""}</small></div>
                )}
              </div>
              {cmp?.note && <p className="hint">{cmp.note}</p>}
              {cmp?.measurement_note && <p className="note">{cmp.measurement_note}</p>}
              {current.measurement_id && <p className="note-ok">Stored as measurement {current.measurement_id} for the host-cpu profile; calibration was refitted. Compare it in the Predicted vs measured tab.</p>}
              {r.notes?.map((n) => <p key={n} className="hint">{n}</p>)}
            </>
          )}
          {cmp?.by_op && cmp.by_op.length > 0 && (
            <Figure title="Time by operator type, predicted vs measured" note="Runtime fusion renames operators, so the comparison groups them by type."
              table={<table className="tbl"><thead><tr><th>Operator type</th><th className="num">Predicted</th><th className="num">Measured</th><th className="num">Error</th></tr></thead>
                <tbody>{cmp.by_op.map((o) => <tr key={o.op}><td>{o.op}</td><td className="num">{ms(o.predicted_ms)}</td><td className="num">{ms(o.measured_ms)}</td><td className="num">{o.error_percent === null ? "–" : `${o.error_percent.toFixed(0)}%`}</td></tr>)}</tbody></table>}>
              <ul className="hbars pair">
                {cmp.by_op.slice(0, 10).map((o) => {
                  const max = Math.max(...cmp.by_op!.map((x) => Math.max(x.predicted_ms, x.measured_ms)), 1e-9);
                  return (
                    <li key={o.op}>
                      <span className="hb-label">{o.op}</span>
                      <span className="hb-pair">
                        <span className="hb-track"><span className="hb-bar" style={{ width: `${(100 * o.predicted_ms) / max}%`, background: "var(--s1)" }} /></span>
                        <span className="hb-track"><span className="hb-bar" style={{ width: `${(100 * o.measured_ms) / max}%`, background: "var(--s2)" }} /></span>
                      </span>
                      <span className="hb-val num">{ms(o.predicted_ms)} / {ms(o.measured_ms)}</span>
                    </li>
                  );
                })}
              </ul>
              <ul className="viz-legend"><li><span className="viz-sw" style={{ background: "var(--s1)" }} />Predicted</li><li><span className="viz-sw" style={{ background: "var(--s2)" }} />Measured</li></ul>
            </Figure>
          )}
          {ops.length > 0 && (
            <details>
              <summary>Slowest measured operators</summary>
              <table className="tbl"><thead><tr><th>Operator</th><th>Type</th><th className="num">Measured time</th></tr></thead>
                <tbody>{ops.map((o) => <tr key={o.index}><td>{o.name}</td><td>{o.op}</td><td className="num">{ms(o.duration_ms)}</td></tr>)}</tbody></table>
            </details>
          )}
        </section>
      )}

      {jobs.length > 1 && (
        <section className="panel">
          <h2>Earlier runs</h2>
          <table className="tbl">
            <thead><tr><th>Run</th><th>When</th><th>Status</th><th className="num">Mean latency</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id}><td>{j.id}</td><td className="muted">{when(j.created_at)}</td><td>{j.status}</td>
                <td className="num">{j.result.latency_ms ? ms(j.result.latency_ms.mean) : "–"}</td>
                <td><button className="link" onClick={() => setCurrent(j)}>Show</button></td></tr>
            ))}</tbody>
          </table>
        </section>
      )}
    </div>
  );
}
