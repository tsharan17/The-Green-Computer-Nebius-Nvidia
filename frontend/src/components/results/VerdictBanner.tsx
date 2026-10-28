import type { Constraints, Verdict, VerdictSide, Workload } from "../../types";
import { fmt, money } from "../../lib/plain";

function Tile({
  label,
  value,
  unit,
  need,
  ok,
}: {
  label: string;
  value: string;
  unit: string;
  need?: string;
  ok?: boolean;
}) {
  return (
    <div className={`tile ${ok === undefined ? "" : ok ? "tile-ok" : "tile-bad"}`}>
      <span className="tile-label">{label}</span>
      <strong>
        {value} <small>{unit}</small>
      </strong>
      {need && <span className="tile-need">{need}</span>}
    </div>
  );
}

function Side({
  title,
  side,
  wl,
  cons,
  tone,
}: {
  title: string;
  side: VerdictSide;
  wl: Workload;
  cons: Constraints;
  tone: "before" | "after";
}) {
  const icon = side.ok ? "✓" : side.state === "uncertain" ? "?" : "✕";
  const kind = side.ok ? "ok" : side.state === "uncertain" ? "warn" : "bad";
  return (
    <div className={`side side-${tone}`}>
      <div className="side-head">
        <span className={`status-dot status-${kind}`} aria-hidden>
          {icon}
        </span>
        <div>
          <span className="side-title">{title}</span>
          <h3>{side.headline}</h3>
        </div>
      </div>
      {side.problems.filter((p) => p !== side.headline).length > 0 && !side.ok && (
        <ul className="problems">
          {side.problems.filter((p) => p !== side.headline).map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="tiles">
        <Tile label="Speed" value={fmt(side.capacity_fps)} unit="frames/s" need={`need ${fmt(wl.fps)}`} ok={side.capacity_fps >= wl.fps} />
        <Tile label="Power" value={fmt(side.power_w)} unit="W" need={`limit ${fmt(cons.max_power_w)} W`} ok={side.power_w <= cons.max_power_w} />
        <Tile
          label="Delay per frame"
          value={fmt(side.latency_ms)}
          unit="ms"
          need={cons.max_latency_ms ? `limit ${fmt(cons.max_latency_ms)} ms` : undefined}
          ok={cons.max_latency_ms ? side.latency_ms <= cons.max_latency_ms : undefined}
        />
        <Tile label="Temperature" value={fmt(side.temperature_c, 0)} unit="°C" />
      </div>
    </div>
  );
}

export default function VerdictBanner({
  verdict,
  wl,
  cons,
}: {
  verdict: Verdict;
  wl: Workload;
  cons: Constraints;
}) {
  const s = verdict.savings;
  return (
    <section className="panel verdict">
      <h2 className="verdict-summary">{verdict.summary}</h2>
      <div className="before-after">
        <Side title="Your current setup" side={verdict.baseline} wl={wl} cons={cons} tone="before" />
        {verdict.improved ? (
          <>
            <div className="arrow" aria-hidden>
              →
            </div>
            <Side title="After our changes" side={verdict.improved} wl={wl} cons={cons} tone="after" />
          </>
        ) : (
          <div className="side side-after side-none">
            <h3>No setting we can change fixes this</h3>
            <p className="hint">Your requirements were not relaxed. See the other options below.</p>
          </div>
        )}
      </div>
      {s && verdict.improved && (
        <div className="savings">
          <div>
            <strong>{s.power_percent >= 0 ? "−" : "+"}{fmt(Math.abs(s.power_percent), 0)}%</strong>
            <span>power</span>
          </div>
          <div>
            <strong>{s.energy_percent >= 0 ? "−" : "+"}{fmt(Math.abs(s.energy_percent), 0)}%</strong>
            <span>energy per frame</span>
          </div>
          <div>
            <strong>{s.cost_per_year >= 0 ? "" : "+"}{money(Math.abs(s.cost_per_year))}</strong>
            <span>electricity cost saved per year</span>
          </div>
          <div>
            <strong>{fmt(Math.abs(s.carbon_kg_per_year), 0)} kg</strong>
            <span>CO₂e {s.carbon_kg_per_year >= 0 ? "avoided" : "added"} per year</span>
          </div>
        </div>
      )}
    </section>
  );
}
