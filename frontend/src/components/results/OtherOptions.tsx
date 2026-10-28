import type { Alternative, Feasibility, WhatIf } from "../../types";
import { STATE_PLAIN, fmt } from "../../lib/plain";

const okState = (s: Feasibility) => s === "predicted_feasible" || s === "verified_feasible";

function AltTable({ title, hint, items }: { title: string; hint: string; items: Alternative[] }) {
  if (!items.length) return null;
  return (
    <div className="alt-block">
      <h3>{title}</h3>
      <p className="hint">{hint}</p>
      <div className="table-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>Option</th>
              <th>Result</th>
              <th>Power</th>
              <th>Speed</th>
              <th>Delay</th>
              <th>Energy vs {items[0].compared_with}</th>
              {items[0].kind === "model" && <th>Accuracy vs current</th>}
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td>
                  <strong>{a.name}</strong>
                  {a.kind === "model" && <div className="muted">{a.lighter ? "lighter" : "heavier"} · {a.note}</div>}
                </td>
                <td>
                  <span className={`chip chip-${okState(a.feasibility) ? "predicted_feasible" : a.feasibility}`}>{STATE_PLAIN[a.feasibility]}</span>
                  {a.problem && <div className="muted">{a.problem}</div>}
                </td>
                <td>{fmt(a.power_w)} W</td>
                <td>{fmt(a.capacity_fps)} frames/s</td>
                <td>{fmt(a.latency_ms)} ms</td>
                <td className={(a.energy_saving_percent ?? 0) >= 0 ? "good" : "bad"}>
                  {a.energy_saving_percent === null ? "–" : `${a.energy_saving_percent >= 0 ? "−" : "+"}${fmt(Math.abs(a.energy_saving_percent), 0)}%`}
                </td>
                {a.kind === "model" && <td>{a.accuracy_retention ? `${fmt(a.accuracy_retention * 100, 0)}%` : "–"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Alternatives({ alternatives }: { alternatives: Alternative[] }) {
  if (!alternatives.length) return null;
  return (
    <section className="panel">
      <h2>Other models and other boards</h2>
      <AltTable
        title="Other AI models"
        hint="Same task, same hardware, same requirements. Accuracy comes from each model's published results."
        items={alternatives.filter((a) => a.kind === "model")}
      />
      <AltTable
        title="Other boards"
        hint="The same AI on different hardware from our library, with each board's own best settings."
        items={alternatives.filter((a) => a.kind === "device")}
      />
    </section>
  );
}

export function DesignIdeas({ whatifs, limiting }: { whatifs: WhatIf[]; limiting: string | null }) {
  if (!whatifs.length) return null;
  const max = Math.max(...whatifs.map((w) => Math.max(Math.abs(w.energy_change_percent), Math.abs(w.latency_change_percent))), 1);
  return (
    <section className="panel">
      <h2>If you could redesign the hardware</h2>
      {limiting && (
        <p className="limit">
          <strong>What holds your current setup back:</strong> {limiting}.
        </p>
      )}
      <p className="hint">
        Each row changes one thing about the chip or board and re-runs your workload. Bars show the effect on energy and
        on speed (a longer bar is a bigger effect). These are model what-ifs: proving one needs chip-level modelling,
        synthesis and, finally, silicon.
      </p>
      <div className="whatifs">
        {whatifs.map((w) => (
          <div key={w.id} className="whatif">
            <div className="whatif-text">
              <strong>{w.title}</strong>
              <span className="muted">{w.change}</span>
              {w.makes_baseline_feasible && <span className="fixes">would also fix your current setup</span>}
            </div>
            <div className="bars">
              <div className="bar-row">
                <span>Energy</span>
                <div className="bar-track">
                  <div className={`bar ${w.energy_change_percent <= 0 ? "bar-good" : "bar-bad"}`} style={{ width: `${(Math.abs(w.energy_change_percent) / max) * 100}%` }} />
                </div>
                <em>{w.energy_change_percent >= 0 ? "+" : "−"}{fmt(Math.abs(w.energy_change_percent), 0)}%</em>
              </div>
              <div className="bar-row">
                <span>Delay</span>
                <div className="bar-track">
                  <div className={`bar ${w.latency_change_percent <= 0 ? "bar-good" : "bar-bad"}`} style={{ width: `${(Math.abs(w.latency_change_percent) / max) * 100}%` }} />
                </div>
                <em>{w.latency_change_percent >= 0 ? "+" : "−"}{fmt(Math.abs(w.latency_change_percent), 0)}%</em>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
