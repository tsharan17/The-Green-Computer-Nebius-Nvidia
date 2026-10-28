import { useEffect, useMemo, useState } from "react";
import { Figure, bytes, ms, num } from "./chart";
import HardwareGraph from "./HardwareGraph";
import Map3DSection from "./map3d/Map3DSection";
import { TraceBar, useTrace } from "./TraceBar";
import type { Component, ExecutionTrace } from "./types";

const VW = 780;
const VH = 500;
const LEFT = { x: 8, w: 230 };
const RIGHT = { x: 290, w: 482, y: 26, h: 464 };

function heatFill(share: number, max: number) {
  const pct = max > 0 ? 8 + 72 * (share / max) : 8;
  return `color-mix(in oklab, var(--heat-fill) ${pct.toFixed(0)}%, var(--panel))`;
}

function rect(c: Component) {
  return {
    x: RIGHT.x + c.layout.x * RIGHT.w,
    y: RIGHT.y + c.layout.y * RIGHT.h,
    w: c.layout.w * RIGHT.w,
    h: c.layout.h * RIGHT.h,
  };
}

function MapSvg({ t, active, onPickStage }: { t: ExecutionTrace; active: number; onPickStage: (first: number) => void }) {
  const ev = t.events[active];
  const comps = useMemo(() => new Map(t.components.map((c) => [c.id, c])), [t]);
  const maxHeat = Math.max(...t.components.map((c) => c.heat_share), 0);
  const stages = t.stages;
  const busy = stages.reduce((a, s) => a + s.duration_ms, 0) || 1;
  const rowH = Math.min(46, (VH - 40) / Math.max(stages.length, 1));
  const maxStagePower = Math.max(...stages.map((s) => (s.duration_ms ? s.energy_mj / s.duration_ms : 0)), 0);
  const flagged = new Set(t.bottlenecks.map((b) => b.component).filter(Boolean) as string[]);
  const compute = comps.get("compute");
  const cr = compute ? rect(compute) : null;
  const activeStage = stages.find((s) => s.index === ev?.stage);
  const pathFrom = new Map(t.paths.map((p) => [p.source, p]));

  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="sysmap" role="group" aria-label={`System map of ${t.model_name} on ${t.device_name}`}>
      <text x={LEFT.x} y={16} className="map-col">Model, by stage (darker = more power)</text>
      <text x={RIGHT.x} y={16} className="map-col">{t.device_name}</text>
      {stages.map((s, i) => {
        const y = 28 + i * rowH;
        const p = s.duration_ms ? s.energy_mj / s.duration_ms : 0;
        const on = activeStage?.index === s.index;
        return (
          <g key={s.index} className={`map-stage ${on ? "on" : ""}`} onClick={() => onPickStage(s.first_event)} role="button" tabIndex={0}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPickStage(s.first_event)}>
            <title>{`${s.name}: ${ms(s.duration_ms)}, ${num(s.energy_mj)} mJ, mostly ${s.dominant_op}`}</title>
            <rect x={LEFT.x} y={y} width={LEFT.w} height={rowH - 6} rx={4} className="map-stage-track" />
            <rect x={LEFT.x} y={y} width={LEFT.w} height={rowH - 6} rx={4} style={{ fill: heatFill(p, maxStagePower) }} />
            {on && ev && (
              <rect x={LEFT.x + ((ev.index - s.first_event + 0.5) / (s.last_event - s.first_event + 1)) * LEFT.w - 2} y={y + rowH - 10} width={4} height={4} rx={2} className="map-cursor" />
            )}
            <text x={LEFT.x + 8} y={y + (rowH - 6) / 2 + 4} className="map-label">{`${i + 1}. ${s.dominant_op}`}</text>
            <text x={LEFT.x + LEFT.w - 8} y={y + (rowH - 6) / 2 + 4} textAnchor="end" className="map-sub">{`${ms(s.duration_ms)}, ${((100 * s.duration_ms) / busy).toFixed(0)}%`}</text>
          </g>
        );
      })}

      {cr && activeStage && (
        <path
          className="map-exec"
          d={`M${LEFT.x + LEFT.w},${28 + stages.indexOf(activeStage) * rowH + (rowH - 6) / 2} C${(LEFT.x + LEFT.w + cr.x) / 2},${28 + stages.indexOf(activeStage) * rowH} ${(LEFT.x + LEFT.w + cr.x) / 2},${cr.y + cr.h / 2} ${cr.x},${cr.y + cr.h / 2}`}
        />
      )}

      {t.paths.map((p) => {
        const a = comps.get(p.source);
        const b = comps.get(p.target);
        if (!a || !b) return null;
        const ra = rect(a);
        const rb = rect(b);
        const util = ev && ev.kind !== "idle" ? ev.bandwidth_utilisation : 0;
        const x1 = ra.x < rb.x ? ra.x + ra.w : ra.x;
        const x2 = ra.x < rb.x ? rb.x : rb.x + rb.w;
        const ym = (ra.y + ra.h / 2 + rb.y + rb.h / 2) / 2;
        return (
          <g key={p.id} className="map-path">
            <title>{`${p.label}: ${num(p.bandwidth_gbs)} GB/s available, ${(util * 100).toFixed(0)}% used by the current operator, peak ${(p.peak_utilisation * 100).toFixed(0)}%`}</title>
            <line x1={x1} y1={ym} x2={x2} y2={ym} className="map-path-base" />
            <line x1={x1} y1={ym} x2={x2} y2={ym} className="map-path-use" style={{ strokeWidth: 2 + 10 * Math.min(1, util) }} />
          </g>
        );
      })}

      {t.components.map((c) => {
        const r = rect(c);
        const used = ev?.components.includes(c.id);
        const flag = flagged.has(c.id);
        return (
          <g key={c.id} className={`map-comp ${used ? "used" : ""} ${flag ? "flag" : ""}`}>
            <title>{`${c.name}: ${c.detail}. Average heat ${num(c.heat_w)} W (${(c.heat_share * 100).toFixed(0)}% of the total).`}</title>
            <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={6} style={{ fill: heatFill(c.heat_share, maxHeat) }} />
            <text x={r.x + 10} y={r.y + 20} className="map-label">{c.name.length > 22 ? `${c.name.slice(0, 21)}…` : c.name}</text>
            {c.detail.split(", ").slice(0, 2).map((line, k) => (
              <text key={k} x={r.x + 10} y={r.y + 38 + k * 15} className="map-sub">{line.length > 28 ? `${line.slice(0, 27)}…` : line}</text>
            ))}
            {c.heat_w > 0 && <text x={r.x + 10} y={r.y + r.h - 10} className="map-sub">{`${num(c.heat_w)} W heat`}</text>}
            {pathFrom.get(c.id) !== undefined && ev?.kind !== "idle" && (
              <text x={r.x + 10} y={r.y + r.h - 26} className="map-sub">{`bandwidth ${(100 * (ev?.bandwidth_utilisation ?? 0)).toFixed(0)}% used`}</text>
            )}
            {flag && (
              <text x={r.x + r.w - 10} y={r.y + r.h - 10} textAnchor="end" className="map-flag">⚠ bottleneck</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export default function SystemMapPage({ projectId, version, hasBest }: { projectId: number; version: number; hasBest: boolean }) {
  const state = useTrace(projectId, version, hasBest);
  const t = state.data?.trace;
  const ops = t ? t.events.filter((e) => e.kind !== "idle") : [];
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => setI(0), [t]);
  useEffect(() => {
    if (!playing || !ops.length) return;
    const id = window.setInterval(() => setI((x) => Math.min(x + 1, ops.length - 1)), 180);
    return () => window.clearInterval(id);
  }, [playing, ops.length]);
  useEffect(() => {
    if (playing && i >= ops.length - 1) setPlaying(false);
  }, [playing, i, ops.length]);
  const ev = ops[Math.min(i, ops.length - 1)];

  return (
    <div className="twin-page">
      <TraceBar projectId={projectId} state={state} hasBest={hasBest} csvTable="events" />
      {t && ev && (
        <>
          <Figure
            title="System map"
            note="Model stages on the left, the hardware on the right. Fill shows where the heat goes (darker = more); memory paths thicken with the bandwidth the current operator uses. Step through the run with the slider."
            table={
              <table className="tbl">
                <thead><tr><th>Component</th><th>Detail</th><th className="num">Heat (W)</th><th className="num">Share</th></tr></thead>
                <tbody>{t.components.map((c) => (
                  <tr key={c.id}><td>{c.name}</td><td>{c.detail}</td><td className="num">{num(c.heat_w)}</td><td className="num">{(c.heat_share * 100).toFixed(0)}%</td></tr>
                ))}</tbody>
              </table>
            }
          >
            <MapSvg t={t} active={ev.index} onPickStage={(first) => setI(ops.findIndex((e) => e.index === first))} />
            <div className="scrub">
              <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play through the operators"}>{playing ? "Pause" : "Play"}</button>
              <input
                type="range"
                min={0}
                max={ops.length - 1}
                value={Math.min(i, ops.length - 1)}
                onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }}
                aria-label="Operator"
                aria-valuetext={`${ev.name}, operator ${i + 1} of ${ops.length}`}
              />
              <span className="num muted">{i + 1} / {ops.length}</span>
            </div>
            <dl className="event-card">
              <div><dt>Operator</dt><dd>{ev.name} <span className="muted">({ev.op})</span></dd></div>
              <div><dt>Starts at</dt><dd className="num">{ms(ev.start_ms)}</dd></div>
              <div><dt>Takes</dt><dd className="num">{ms(ev.duration_ms)}</dd></div>
              <div><dt>Limited by</dt><dd>{ev.bound}</dd></div>
              <div><dt>Power</dt><dd className="num">{num(ev.power_w)} W</dd></div>
              <div><dt>Working memory</dt><dd className="num">{bytes(ev.working_set_bytes)}</dd></div>
              <div><dt>Bandwidth used</dt><dd className="num">{(ev.bandwidth_utilisation * 100).toFixed(0)}%</dd></div>
              <div><dt>Energy</dt><dd className="num">{num(Object.values(ev.energy_mj).reduce((a, b) => a + b, 0))} mJ</dd></div>
            </dl>
          </Figure>
          <section className="panel" aria-labelledby="bn-h">
            <h2 id="bn-h">Where it is limited</h2>
            {t.bottlenecks.length === 0 ? (
              <p className="empty">No single operator or component dominates at this setting.</p>
            ) : (
              <ul className="bottlenecks">
                {t.bottlenecks.map((b, k) => (
                  <li key={k}>
                    <strong>{b.title}</strong> <span className="badge">{b.kind}</span>
                    <p>{b.detail}</p>
                    {b.event !== null && <button className="link" onClick={() => setI(ops.findIndex((e) => e.index === b.event))}>Show it on the map</button>}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <details className="panel">
            <summary>Assumptions behind this trace</summary>
            <ul>{t.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            <p className="hint">{t.fidelity_note}</p>
          </details>
        </>
      )}
      <Map3DSection projectId={projectId} state={state} hasBest={hasBest} />
      <HardwareGraph projectId={projectId} />
    </div>
  );
}
