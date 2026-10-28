import type { VerifyRow } from "../types";
import { fmt } from "./common";

const W = 520;
const H = 420;
const M = { l: 58, r: 16, t: 14, b: 46 };

function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** e;
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  return out;
}

/** Measured (x) vs predicted (y) batch latency on log axes. On the diagonal = perfect. */
export default function PredVsMeasuredChart({ rows }: { rows: VerifyRow[] }) {
  if (!rows.length) return null;
  const all = rows.flatMap((r) => [r.measurement.latency_ms_mean ?? 0, r.predicted_ms, r.predicted_cal_ms ?? 0]).filter((v) => v > 0);
  const lo = Math.min(...all) * 0.7;
  const hi = Math.max(...all) * 1.4;
  const sx = (v: number) => M.l + ((Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))) * (W - M.l - M.r);
  const sy = (v: number) => H - M.b - ((Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))) * (H - M.t - M.b);
  const ticks = logTicks(lo, hi);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Predicted versus measured latency">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={sy(v)} y2={sy(v)} className="grid" />
            <line y1={M.t} y2={H - M.b} x1={sx(v)} x2={sx(v)} className="grid" />
            <text x={M.l - 8} y={sy(v) + 4} textAnchor="end" className="axis">{fmt(v)}</text>
            <text x={sx(v)} y={H - M.b + 18} textAnchor="middle" className="axis">{fmt(v)}</text>
          </g>
        ))}
        <line x1={sx(lo)} y1={sy(lo)} x2={sx(hi)} y2={sy(hi)} className="line-limit" style={{ stroke: "var(--muted)" }} />
        <text x={sx(hi) - 6} y={sy(hi) + 14} textAnchor="end" className="axis">perfect prediction</text>
        <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className="axis">measured batch latency (ms)</text>
        <text transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" className="axis">predicted (ms)</text>
        {rows.map((r) => {
          const meas = r.measurement.latency_ms_mean;
          if (!meas) return null;
          const tip = `${r.measurement.model_id} · batch ${r.measurement.batch} · measured ${fmt(meas)} ms`;
          return (
            <g key={r.measurement.id}>
              <circle cx={sx(meas)} cy={sy(r.predicted_ms)} r={5} className="pt-uncal">
                <title>{`${tip} · uncalibrated ${fmt(r.predicted_ms)} ms`}</title>
              </circle>
              {r.predicted_cal_ms !== null && (
                <circle cx={sx(meas)} cy={sy(r.predicted_cal_ms)} r={4.5} className="pt-cal">
                  <title>{`${tip} · calibrated (held out) ${fmt(r.predicted_cal_ms)} ms`}</title>
                </circle>
              )}
            </g>
          );
        })}
      </svg>
      <p className="legend">
        <span><i className="sw sw-open" />before calibration</span>
        <span><i className="sw sw-ok" />after calibration (each point predicted without itself)</span>
      </p>
    </div>
  );
}
