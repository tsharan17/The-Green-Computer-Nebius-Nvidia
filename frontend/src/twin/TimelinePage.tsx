import { useState } from "react";
import { Figure, Legend, ms, num, useTip } from "./chart";
import { TraceBar, useTrace } from "./TraceBar";
import type { ExecutionTrace, TraceEvent } from "./types";

const BOUND_COLOR: Record<string, string> = { compute: "var(--s1)", memory: "var(--s2)", overhead: "var(--s3)", idle: "var(--line-strong)" };
const BOUND_LABEL: Record<string, string> = { compute: "Compute-bound", memory: "Memory-bound", overhead: "Launch overhead", idle: "Idle" };

function Gantt({ t, withIdle }: { t: ExecutionTrace; withIdle: boolean }) {
  const tip = useTip();
  const events = t.events.filter((e) => withIdle || e.kind !== "idle");
  const end = Math.max(...events.map((e) => e.start_ms + e.duration_ms), 1e-9);
  const lanes = t.stages.length || 1;
  const W = 960;
  const L = 150;
  const laneH = 26;
  const H = (lanes + (withIdle ? 1 : 0)) * laneH + 40;
  const x = (v: number) => L + (v / end) * (W - L - 10);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * end);
  const lane = (e: TraceEvent) => (e.kind === "idle" ? lanes : e.stage);

  return (
    <div className="viz-box" ref={tip.box}>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="group" aria-label="Operator timeline">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={x(v)} x2={x(v)} y1={8} y2={H - 24} className="grid" />
            <text x={x(v)} y={H - 8} textAnchor="middle" className="axis">{ms(v)}</text>
          </g>
        ))}
        {t.stages.map((s) => (
          <text key={s.index} x={L - 8} y={14 + s.index * laneH + laneH / 2} textAnchor="end" className="axis">
            {`${s.index + 1}. ${s.dominant_op}`}
          </text>
        ))}
        {withIdle && <text x={L - 8} y={14 + lanes * laneH + laneH / 2} textAnchor="end" className="axis">idle</text>}
        {events.map((e) => {
          const w = Math.max(1.2, x(e.start_ms + e.duration_ms) - x(e.start_ms) - 1);
          return (
            <rect
              key={e.index}
              x={x(e.start_ms)}
              y={10 + lane(e) * laneH}
              width={w}
              height={laneH - 8}
              rx={Math.min(3, w / 2)}
              style={{ fill: BOUND_COLOR[e.bound] }}
              tabIndex={0}
              onMouseMove={(m) => tip.show(m, <EventTip e={e} />)}
              onFocus={(f) => tip.show(f, <EventTip e={e} />)}
              onMouseLeave={tip.hide}
              onBlur={tip.hide}
            />
          );
        })}
      </svg>
      {tip.node}
    </div>
  );
}

function EventTip({ e }: { e: TraceEvent }) {
  return (
    <>
      <strong>{e.name}</strong> <span className="muted">{e.op}</span>
      <div>Starts {ms(e.start_ms)}, takes {ms(e.duration_ms)}</div>
      {e.kind !== "idle" && (
        <div>
          compute {ms(e.compute_ms)}, memory {ms(e.memory_ms)}, launch {ms(e.overhead_ms)}
        </div>
      )}
      <div>{BOUND_LABEL[e.bound]}, {num(e.power_w)} W</div>
    </>
  );
}

export default function TimelinePage({ projectId, version, hasBest }: { projectId: number; version: number; hasBest: boolean }) {
  const state = useTrace(projectId, version, hasBest);
  const t = state.data?.trace;
  const [withIdle, setWithIdle] = useState(false);
  if (!t) return <div className="twin-page"><TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="events" /></div>;

  const ops = t.events.filter((e) => e.kind !== "idle");
  const by = (b: string) => ops.filter((e) => e.bound === b).reduce((a, e) => a + e.duration_ms, 0);
  const launch = ops.reduce((a, e) => a + e.overhead_ms, 0);
  const idle = t.events.filter((e) => e.kind === "idle").reduce((a, e) => a + e.duration_ms, 0);
  const slow = [...ops].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 12);
  const queue = Math.max(0, t.totals.latency_ms - t.totals.batch_ms);

  return (
    <div className="twin-page">
      <TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="events" />
      <div className="kpis">
        <div><span>Latency per input</span><strong className="num">{ms(t.totals.latency_ms)}</strong><small>{queue > 0 ? `incl. ${ms(queue)} waiting for the batch` : "no batching wait"}</small></div>
        <div><span>Batch of {t.batch}</span><strong className="num">{ms(t.totals.batch_ms)}</strong><small>{ops.length} operators</small></div>
        <div><span>Busy</span><strong className="num">{(t.totals.busy_fraction * 100).toFixed(0)}%</strong><small>of each {ms(t.totals.period_ms)} period</small></div>
        <div><span>Launch overhead</span><strong className="num">{ms(launch)}</strong><small>{((launch / t.totals.batch_ms) * 100).toFixed(0)}% of the batch</small></div>
      </div>
      <Figure
        title="Execution timeline"
        note="One bar per operator, grouped by stage; colour says what limits it. Times are predicted from the roofline at the solved clock."
        actions={<label className="check-row"><input type="checkbox" checked={withIdle} onChange={(e) => setWithIdle(e.target.checked)} /> Show idle time</label>}
        table={
          <table className="tbl">
            <thead><tr><th>#</th><th>Operator</th><th>Stage</th><th className="num">Start</th><th className="num">Duration</th><th>Limited by</th></tr></thead>
            <tbody>{t.events.map((e) => (
              <tr key={e.index}><td className="num">{e.index}</td><td>{e.name}</td><td className="num">{e.stage + 1}</td><td className="num">{ms(e.start_ms)}</td><td className="num">{ms(e.duration_ms)}</td><td>{BOUND_LABEL[e.bound]}</td></tr>
            ))}</tbody>
          </table>
        }
      >
        <Legend items={[
          { label: "Compute-bound", color: BOUND_COLOR.compute, value: ms(by("compute")) },
          { label: "Memory-bound", color: BOUND_COLOR.memory, value: ms(by("memory")) },
          { label: "Overhead-bound", color: BOUND_COLOR.overhead, value: ms(by("overhead")) },
          ...(withIdle ? [{ label: "Idle", color: BOUND_COLOR.idle, value: ms(idle) }] : []),
        ]} />
        <Gantt t={t} withIdle={withIdle} />
      </Figure>
      <section className="panel" aria-labelledby="slow-h">
        <h2 id="slow-h">Slowest operators</h2>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Operator</th><th>Type</th><th className="num">Time</th><th className="num">Share</th><th className="num">Compute</th><th className="num">Memory</th><th>Limited by</th><th className="num">Bandwidth used</th></tr></thead>
            <tbody>{slow.map((e) => (
              <tr key={e.index}>
                <td>{e.name}</td><td>{e.op}</td><td className="num">{ms(e.duration_ms)}</td>
                <td className="num">{((e.duration_ms / t.totals.batch_ms) * 100).toFixed(1)}%</td>
                <td className="num">{ms(e.compute_ms)}</td><td className="num">{ms(e.memory_ms)}</td>
                <td>{BOUND_LABEL[e.bound]}</td><td className="num">{(e.bandwidth_utilisation * 100).toFixed(0)}%</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
