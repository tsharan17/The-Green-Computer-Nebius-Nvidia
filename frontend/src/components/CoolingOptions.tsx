import { useEffect, useState } from "react";
import { plans } from "../api";
import { navigate } from "../router";
import { num } from "../twin/chart";
import type { CoolingReport } from "../types";
import { ErrorNote, Level } from "./Help";

const RATING: Record<string, { cls: string; icon: string }> = {
  "lower impact": { cls: "ok", icon: "✓" },
  "meets requirements": { cls: "warn", icon: "○" },
  "does not meet requirements": { cls: "bad", icon: "✕" },
};
const ENV_TEXT: Record<string, string> = {
  sealed_enclosure: "sealed enclosure", dusty: "dusty site", datacenter: "data-centre rack", facility_water: "facility water",
};

function envSummary(env: CoolingReport["environment"]) {
  if (!env) return "no limits set";
  const parts = Object.entries(ENV_TEXT).filter(([k]) => env[k as keyof typeof env]).map(([, v]) => v);
  if (env.max_noise_dba !== null) parts.push(`at most ${env.max_noise_dba} dBA`);
  if (env.max_height_mm !== null) parts.push(`at most ${env.max_height_mm} mm above the board`);
  return parts.length ? parts.join(", ") : "no limits set";
}

export default function CoolingOptions({ projectId, which }: { projectId: number; which: "baseline" | "best" }) {
  const [data, setData] = useState<CoolingReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [onlyOk, setOnlyOk] = useState(true);

  useEffect(() => {
    setData(null);
    plans.cooling(projectId, which).then((d) => setData(d as CoolingReport)).catch(setError);
  }, [projectId, which]);

  if (error) return <section className="panel"><h2>Cooling options for this workload</h2><ErrorNote error={error} /></section>;
  if (!data) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /></div>;
  const rows = data.rows.filter((r) => !onlyOk || r.meets_requirements || r.id === "current");
  const top = data.rows.find((r) => r.rating === "lower impact");
  const current = data.rows.find((r) => r.id === "current");
  const excluded = data.excluded ?? [];
  const local = excluded.filter((x) => !x.reasons[0]?.includes("not this kind of device"));
  return (
    <section className="panel" aria-labelledby="cool-h">
      <div className="row">
        <h2 id="cool-h">Cooling options for this workload</h2>
        <label className="check-row"><input type="checkbox" checked={onlyOk} onChange={(e) => setOnlyOk(e.target.checked)} /> Only options that meet your requirements</label>
      </div>
      <p className="tn-summary">
        {top ? <>Best fit here: <strong>{top.name}</strong>, {num(top.temperature_c)} °C{top.throttled ? " (throttles)" : " without throttling"},
          {" "}{num(top.lifetime_kgco2e_per_year)} kgCO₂e a year.</> : <>No catalogue option meets every requirement where this device runs.</>}
        {current && !current.meets_requirements && " The cooling fitted today does not."}
      </p>
      <p className="hint">
        Where it runs: {envSummary(data.environment)}. <a href={`#/p/${projectId}/constraints`}>Change this</a>.
        {local.length > 0 && ` ${local.length} option${local.length === 1 ? " is" : "s are"} ruled out there (listed below).`}
      </p>

      {(data.heat_sources ?? []).length > 0 && (
        <Level kind="explain" title="Where the heat starts, and what reaches it">
          <ul className="heat-sources">
            {data.heat_sources!.map((h) => (
              <li key={h.source}>
                <strong>{h.name}</strong> <span className="num">{num(h.heat_w)} W ({(h.share * 100).toFixed(0)}%)</span>
                <p>{h.why}</p>
                <p className="muted">{h.reached_by.length ? `Reached by: ${h.reached_by.join(", ")}.` : "No usable option here reaches it directly."}</p>
              </li>
            ))}
          </ul>
          <p className="hint">Heat split from the multi-node thermal view ({data.heat_sources![0].label}).</p>
        </Level>
      )}

      <p className="hint">Ranked by these stated criteria, in order:</p>
      <ol className="criteria">{data.criteria.map((c) => <li key={c}>{c}</li>)}</ol>
      <div className="table-wrap">
        <table className="tbl cooling">
          <thead>
            <tr><th>Option</th><th>Rating</th><th className="num">R_th (K/W)</th><th className="num">Fan/pump</th><th className="num">Temperature</th><th className="num">System power</th><th className="num">kgCO₂e / year</th><th>Sustainability and upkeep</th><th><span className="sr-only">Plan</span></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const rt = RATING[r.rating] ?? { cls: "none", icon: "" };
              const s = r.sustainability;
              return (
                <tr key={r.id}>
                  <td>
                    <strong>{r.name}</strong>
                    <small className="block muted">{r.materials}{r.cost_usd ? `, about $${r.cost_usd.low}–${r.cost_usd.high}` : ""}{r.noise_dba ? `, ${r.noise_dba} dBA` : ""}</small>
                    {r.heat_share_reached !== null && r.heat_share_reached !== undefined && (
                      <small className="block">Reaches {(r.heat_share_reached * 100).toFixed(0)}% of the heat ({(r.targets ?? []).join(", ")})</small>
                    )}
                    {(r.needs ?? []).length > 0 && <small className="block muted">Needs {r.needs!.join(", ")}.</small>}
                    <small className="block muted">Source: {r.source} ({r.confidence} confidence)</small>
                  </td>
                  <td><span className={`outcome ${rt.cls}`}><span aria-hidden className="outcome-icon">{rt.icon}</span> {r.rating}</span><small className="block muted">{r.explanation}</small></td>
                  <td className="num">{num(r.r_th_k_per_w)}{r.r_th_range && <small className="block muted">{num(r.r_th_range.low)}–{num(r.r_th_range.high)}{r.modifier ? " ×" : ""}</small>}</td>
                  <td className="num">{num(r.active_w)} W</td>
                  <td className="num">{num(r.temperature_c)} °C{r.throttled && <small className="block bad">throttles to {(r.clock_factor * 100).toFixed(0)}%</small>}</td>
                  <td className="num">{num(r.power_w)} W</td>
                  <td className="num">{num(r.lifetime_kgco2e_per_year)}<small className="block muted">{num(r.operational_kgco2e_per_year)} use + {num(r.embodied_kgco2e_per_year)} made</small></td>
                  <td>
                    {s && s.repairability && <small className="block">Repairability {s.repairability}, recyclability {s.recyclability}{s.hazardous && s.hazardous !== "none" ? `, contains ${s.hazardous}` : ""}.</small>}
                    {s && <small className="block muted">Water: {s.water_l_per_year === null ? s.water_note : `${s.water_l_per_year} L/year on site`}. Made: {s.embodied_note}.</small>}
                    <small className="block muted">{r.maintainability}</small>
                    {(r.cautions ?? []).map((c) => <small key={c} className="block warn-text">⚠ {c}</small>)}
                  </td>
                  <td>{r.change_id && r.meets_requirements && (
                    <button className="link" onClick={() => navigate(`/p/${projectId}/recommendations?tab=plan&add=${encodeURIComponent(r.change_id!)}`)}>Add to a plan</button>
                  )}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {excluded.length > 0 && (
        <details className="excluded">
          <summary>Options not considered here ({excluded.length}), and why</summary>
          <ul>{excluded.map((x) => <li key={x.id}><strong>{x.name}</strong>: {x.reasons.join("; ")}.</li>)}</ul>
        </details>
      )}
      <ul className="hint">{data.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
    </section>
  );
}
