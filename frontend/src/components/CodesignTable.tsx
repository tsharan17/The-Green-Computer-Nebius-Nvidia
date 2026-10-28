import type { CoDesign, EvaluationResult } from "../types";
import { Chip, configLabel, fmt } from "./common";

function Cell({ r, base }: { r: EvaluationResult | null; base: EvaluationResult }) {
  if (!r) return <td className="muted">no feasible option</td>;
  const eBase = base.metrics.energy_per_inference.value;
  const e = r.metrics.energy_per_inference.value;
  const saving = (1 - e / eBase) * 100;
  return (
    <td>
      <Chip state={r.feasibility} />
      <div className="cfg">{configLabel(r.config)}</div>
      <div>
        <strong>{fmt(e)}</strong> mJ
        {r !== base && <span className={saving >= 0 ? "good" : "bad"}> ({saving >= 0 ? "−" : "+"}{fmt(Math.abs(saving))}%)</span>}
      </div>
      <div className="muted">
        {fmt(r.metrics.power_avg.value)} W · {fmt(r.metrics.latency.value)} ms · {fmt(r.metrics.temperature.value)} °C
      </div>
    </td>
  );
}

export default function CodesignTable({ cd }: { cd: CoDesign }) {
  return (
    <>
      <table className="tbl codesign">
        <thead>
          <tr>
            <th>Baseline</th>
            <th>Software only</th>
            <th>Hardware only</th>
            <th>Combined (co-design)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <Cell r={cd.baseline} base={cd.baseline} />
            <Cell r={cd.software_only} base={cd.baseline} />
            <Cell r={cd.hardware_only} base={cd.baseline} />
            <Cell r={cd.combined} base={cd.baseline} />
          </tr>
        </tbody>
      </table>
      <p className="note-info">{cd.interpretation}</p>
      <p className="hint">
        Software knobs: precision, batch, input resolution. Hardware knobs: power mode, clock, cooling.
      </p>
    </>
  );
}
