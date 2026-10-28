import { useState } from "react";
import type { EvaluationResult, Feasibility, Point } from "../types";
import { STATE_LABEL, configLabel, fmt } from "./common";

const W = 680;
const H = 360;
const M = { l: 58, r: 18, t: 14, b: 44 };

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

const COLOR: Record<Feasibility, string> = {
  verified_feasible: "var(--ok)",
  predicted_feasible: "var(--ok)",
  uncertain: "var(--warn)",
  infeasible: "var(--bad)",
  verified_infeasible: "var(--bad)",
};

export default function ParetoChart({
  points,
  baseline,
}: {
  points: Point[];
  baseline: EvaluationResult;
}) {
  const [showInfeasible, setShowInfeasible] = useState(false);
  const shown = points.filter((p) => showInfeasible || (p.state !== "infeasible" && p.state !== "verified_infeasible"));
  const bE = baseline.metrics.energy_per_inference.value;
  const bL = baseline.metrics.latency.value;
  const xs = [...shown.map((p) => p.energy_mj), bE];
  const ys = [...shown.map((p) => p.latency_ms), bL];
  const xLo = Math.min(...xs) * 0.8, xHi = Math.max(...xs) * 1.25;
  const yLo = Math.min(...ys) * 0.8, yHi = Math.max(...ys) * 1.25;
  const sx = (v: number) => M.l + ((Math.log(v) - Math.log(xLo)) / (Math.log(xHi) - Math.log(xLo))) * (W - M.l - M.r);
  const sy = (v: number) => H - M.b - ((Math.log(v) - Math.log(yLo)) / (Math.log(yHi) - Math.log(yLo))) * (H - M.t - M.b);

  // Outlined dots: the 4-objective Pareto set (energy, latency, memory, accuracy).
  const front = shown.filter((p) => p.pareto);
  // Line: the 2-D energy/latency envelope of what is shown, drawn as a staircase.
  const byEnergy = [...shown].sort((a, b) => a.energy_mj - b.energy_mj || a.latency_ms - b.latency_ms);
  const env: Point[] = [];
  let minLat = Infinity;
  for (const p of byEnergy) {
    if (p.latency_ms < minLat) {
      env.push(p);
      minLat = p.latency_ms;
    }
  }
  const frontPath = env
    .map((p, i) => {
      const px = sx(p.energy_mj).toFixed(1);
      const py = sy(p.latency_ms).toFixed(1);
      if (!i) return `M${px},${py}`;
      const prevY = sy(env[i - 1].latency_ms).toFixed(1);
      return `L${px},${prevY} L${px},${py}`;
    })
    .join(" ");
  const best = shown.find((p) => p.best);

  return (
    <div>
      <label className="check-row">
        <input type="checkbox" checked={showInfeasible} onChange={(e) => setShowInfeasible(e.target.checked)} />
        Show infeasible configurations
      </label>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Energy versus latency for every evaluated configuration">
        {logTicks(yLo, yHi).map((v) => (
          <g key={`y${v}`}>
            <line x1={M.l} x2={W - M.r} y1={sy(v)} y2={sy(v)} className="grid" />
            <text x={M.l - 8} y={sy(v) + 4} textAnchor="end" className="axis">{fmt(v)}</text>
          </g>
        ))}
        {logTicks(xLo, xHi).map((v) => (
          <g key={`x${v}`}>
            <line y1={M.t} y2={H - M.b} x1={sx(v)} x2={sx(v)} className="grid" />
            <text x={sx(v)} y={H - M.b + 18} textAnchor="middle" className="axis">{fmt(v)}</text>
          </g>
        ))}
        <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className="axis">energy per inference (mJ, lower is better)</text>
        <text transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" className="axis">latency per batch (ms)</text>

        {shown.filter((p) => !p.pareto && !p.best).map((p, i) => (
          <circle key={i} cx={sx(p.energy_mj)} cy={sy(p.latency_ms)} r={2.4} fill={COLOR[p.state]} opacity={0.35}>
            <title>{`${STATE_LABEL[p.state]} · ${configLabel(p.config)} · ${fmt(p.energy_mj)} mJ · ${fmt(p.latency_ms)} ms`}</title>
          </circle>
        ))}
        {frontPath && <path d={frontPath} fill="none" className="line-front" />}
        {front.map((p, i) => (
          <circle key={`f${i}`} cx={sx(p.energy_mj)} cy={sy(p.latency_ms)} r={4.2} fill={COLOR[p.state]} className="pt-front">
            <title>{`Pareto · ${STATE_LABEL[p.state]} · ${configLabel(p.config)} · ${fmt(p.energy_mj)} mJ · ${fmt(p.latency_ms)} ms · accuracy ${fmt(p.accuracy)}`}</title>
          </circle>
        ))}
        <rect
          x={sx(bE) - 5} y={sy(bL) - 5} width={10} height={10}
          transform={`rotate(45 ${sx(bE)} ${sy(bL)})`} className="pt-base"
        >
          <title>{`Baseline · ${configLabel(baseline.config)} · ${fmt(bE)} mJ · ${fmt(bL)} ms`}</title>
        </rect>
        {best && (
          <circle cx={sx(best.energy_mj)} cy={sy(best.latency_ms)} r={8} className="pt-best">
            <title>{`Recommended · ${configLabel(best.config)}`}</title>
          </circle>
        )}
      </svg>
      <p className="legend">
        <span><i className="sw sw-ok" />feasible</span>
        <span><i className="sw sw-warn" />uncertain</span>
        {showInfeasible && <span><i className="sw sw-bad" />infeasible</span>}
        <span><i className="sw sw-front" />Pareto set (energy, latency, memory, accuracy)</span>
        <span><i className="sw-line" />energy–latency envelope</span>
        <span><i className="sw sw-base" />baseline</span>
        <span><i className="sw sw-best" />recommended</span>
      </p>
    </div>
  );
}
