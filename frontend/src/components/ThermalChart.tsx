import type { EvaluationResult } from "../types";
import { fmt } from "./common";

const W = 640;
const H = 280;
const M = { l: 52, r: 16, t: 14, b: 40 };

function fmtTime(s: number): string {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${(s / 60).toFixed(s < 600 ? 1 : 0)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export default function ThermalChart({
  r,
  ambientC,
}: {
  r: EvaluationResult;
  ambientC: number;
}) {
  const trace = r.thermal_trace;
  if (!trace.length) return <p className="hint">No thermal trace for this configuration.</p>;

  const throttleC = r.metrics.thermal_headroom.value + r.metrics.temperature_unthrottled.value;
  const tMax = trace[trace.length - 1][0] || 1;
  const yMin = Math.floor((Math.min(ambientC, trace[0][1]) - 5) / 10) * 10;
  const yMax = Math.ceil((Math.max(throttleC, ...trace.map((p) => p[1])) + 8) / 10) * 10;
  const x = (t: number) => M.l + (t / tMax) * (W - M.l - M.r);
  const y = (v: number) => H - M.b - ((v - yMin) / (yMax - yMin)) * (H - M.t - M.b);
  const path = trace.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  const yTicks: number[] = [];
  for (let v = yMin; v <= yMax; v += 10) yTicks.push(v);
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * tMax);
  const tts = r.time_to_throttle_s;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Temperature over time under sustained load">
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="grid" />
          <text x={M.l - 8} y={y(v) + 4} textAnchor="end" className="axis">{v}°</text>
        </g>
      ))}
      {xTicks.map((t) => (
        <text key={t} x={x(t)} y={H - M.b + 18} textAnchor="middle" className="axis">{fmtTime(t)}</text>
      ))}
      <line x1={M.l} x2={W - M.r} y1={y(ambientC)} y2={y(ambientC)} className="line-ambient" />
      <text x={W - M.r - 4} y={y(ambientC) - 5} textAnchor="end" className="axis">ambient {fmt(ambientC)}°</text>
      <line x1={M.l} x2={W - M.r} y1={y(throttleC)} y2={y(throttleC)} className="line-limit" />
      <text x={M.l + 6} y={y(throttleC) - 5} className="axis axis-bad">throttle point {fmt(throttleC)}°</text>
      <path d={path} className="line-temp" fill="none" />
      {tts !== null && tts !== undefined && tts <= tMax && (
        <g>
          <circle cx={x(tts)} cy={y(throttleC)} r={5} className="dot-bad" />
          <text x={x(tts) + 8} y={y(throttleC) + 16} className="axis">throttles after {fmtTime(tts)}</text>
        </g>
      )}
      <text x={(M.l + W - M.r) / 2} y={H - 4} textAnchor="middle" className="axis">time under sustained load</text>
    </svg>
  );
}
