import { useEffect, useState } from "react";
import { api } from "../api";
import type { Config, LayerProfile } from "../types";
import { fmt } from "./common";

export default function LayerView({ projectId, config, title }: { projectId: number; config: Config; title: string }) {
  const [p, setP] = useState<LayerProfile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let live = true;
    setP(null);
    api.layers(projectId, config).then((x) => live && setP(x)).catch((e) => live && setErr(String(e)));
    return () => { live = false; };
  }, [projectId, config]);

  if (err) return <p className="error">{err}</p>;
  if (!p) return <p className="hint">Loading the layer view…</p>;
  if (!p.has_graph) {
    return (
      <section className="panel">
        <h2>Where the time goes</h2>
        <p className="hint">{p.note}</p>
      </section>
    );
  }
  const rows = all ? p.layers : p.top ?? [];
  const max = Math.max(...rows.map((r) => r.time_ms), 1e-9);
  return (
    <section className="panel">
      <h2>Where the time goes · {title}</h2>
      <p className="hint">
        {p.n_layers} layers, {fmt(p.total_ms)} ms per batch. Graph from {p.graph_source}. Analytical, not profiled; quantisation risk is a
        heuristic until you run the sensitivity script from the recipe.
      </p>
      {(p.hints ?? []).map((h) => <p key={h} className="note">{h}</p>)}
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Layer</th><th>Time</th><th>Share</th><th>Limited by</th><th>int8 risk</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.index}>
                <td>{r.name} <span className="muted">({r.op})</span></td>
                <td>
                  <span className="bar" style={{ width: `${Math.max(2, (r.time_ms / max) * 80)}px` }} /> {fmt(r.time_ms)} ms
                </td>
                <td>{(r.share * 100).toFixed(1)}%</td>
                <td>{r.bound}</td>
                <td className={r.quant_risk >= 0.6 ? "risk-high" : "muted"}>{r.quant_risk >= 0.6 ? "fragile" : r.quant_risk >= 0.35 ? "watch" : "robust"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="cta-row">
        <button onClick={() => setAll(!all)}>{all ? "Show the 10 slowest" : `Show all ${p.layers.length} layers`}</button>
      </div>
      <h3>By operator</h3>
      <ul className="plain-list">
        {p.by_op.slice(0, 8).map((o) => (
          <li key={o.op}><strong>{o.op}</strong> · {o.count} layers · {fmt(o.time_ms)} ms · {(o.share * 100).toFixed(0)}%</li>
        ))}
      </ul>
    </section>
  );
}
