import { Fragment, useEffect, useState } from "react";
import { type Evidence, type Physics, physics } from "../api";
import { ErrorNote, Level, Why } from "../components/Help";
import { bytes, num } from "./chart";

type Which = "baseline" | "best";

function usePhysics(projectId: number, which: Which, version: number) {
  const [data, setData] = useState<Physics | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    physics.get(projectId, which, version).then((d) => live && setData(d)).catch((e) => live && setError(e));
    return () => { live = false; };
  }, [projectId, which, version]);
  return { data, error };
}

const STATUS: Record<string, { icon: string; cls: string; text: string }> = {
  "lumped only": { icon: "○", cls: "none", text: "Predicted only: no measured temperature for this setup yet" },
  validated: { icon: "✓", cls: "ok", text: "Validated against a measured temperature" },
  "not validated": { icon: "✕", cls: "bad", text: "Disagrees with a measured temperature" },
};

/** Multi-node thermal attribution on the Thermal page. */
export function ThermalNodes({ projectId, which, version }: { projectId: number; which: Which; version: number }) {
  const { data, error } = usePhysics(projectId, which, version);
  if (error) return <section className="panel"><h2>Heat by part</h2><ErrorNote error={error} /></section>;
  if (!data) return <section className="panel"><h2>Heat by part</h2><div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div></section>;
  const th = data.thermal;
  if (!th) return <section className="panel"><h2>Heat by part</h2><p className="hint">This trace is measured and has no temperature log, so there is nothing to split.</p></section>;
  const s = STATUS[th.status];
  const hot = th.nodes.find((n) => n.id === th.hottest)!;
  const parts = th.nodes.slice(0, 3);
  const max = Math.max(...th.nodes.map((n) => n.temperature_c), th.lumped_c);
  return (
    <section className="panel thermal-nodes" aria-labelledby="tn-h">
      <h2 id="tn-h">Heat by part</h2>
      <p className="tn-summary">
        The decision model puts the chip at <strong className="num">{num(th.lumped_c)} °C</strong> (lumped).
        Split over the parts, the {hot.name.toLowerCase()} sits at <span className="num">{num(hot.temperature_c)} °C</span> (attribution).
      </p>
      <p><span className={`outcome ${s.cls}`}><span aria-hidden className="outcome-icon">{s.icon}</span> {s.text}</span>
        {th.measured && <span className="muted"> · measured {num(th.measured.temperature_c)} °C ({th.measured.source}), {th.measured.difference_c > 0 ? "prediction higher" : "prediction lower"} by {num(Math.abs(th.measured.difference_c))} °C</span>}
      </p>
      <ul className="temp-ladder" aria-label="Temperature of each part">
        {[{ id: "lumped", name: "Chip (lumped, decides pass/fail)", temperature_c: th.lumped_c, heat_w: th.lumped_power_w, label: "lumped" }, ...th.nodes].map((n) => (
          <li key={n.id}>
            <span className="tl-name">{n.name}</span>
            <span className="tl-track"><span className={`tl-bar ${n.label}`} style={{ width: `${(100 * (n.temperature_c - th.ambient_c)) / Math.max(max - th.ambient_c, 1)}%` }} /></span>
            <span className="num tl-val">{num(n.temperature_c)} °C</span>
            <span className={`tag ${n.label}`}>{n.label}</span>
          </li>
        ))}
      </ul>
      <p className="hint">The voltage regulators' temperature depends on an assumed coupling to the board; treat it as a prompt to check, not a finding. Room at {num(th.ambient_c)} °C. Heat put in: {parts.map((n) => `${n.name.toLowerCase()} ${num(n.heat_w)} W`).join(", ")}.</p>
      <Level kind="explain" title="What the labels mean">
        <dl className="kv">{Object.entries(th.labels).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
        <Why id="thermal-resistance" />
      </Level>
      <Level kind="engineering" title="Thermal network">
        <table className="tbl">
          <thead><tr><th>From</th><th>To</th><th className="num">K/W</th><th>Source</th><th>Note</th></tr></thead>
          <tbody>{th.resistors.map((r) => (
            <tr key={`${r.a}-${r.b}`}><td>{r.a}</td><td>{r.b}</td><td className="num">{num(r.k_per_w)}</td><td>{r.source}</td><td>{r.note}</td></tr>
          ))}</tbody>
        </table>
        <p className="hint">Heat inputs were scaled by {num(th.heat_scale)} so they add up to the lumped model's {num(th.lumped_power_w)} W. Profiles can state r_jc_k_per_w and r_cs_k_per_w in their extras to replace the assumed values.</p>
      </Level>
    </section>
  );
}

/** Runtime effects and memory hierarchy on the Energy page. */
export function RuntimeAndMemory({ projectId, which, version }: { projectId: number; which: Which; version: number }) {
  const { data, error } = usePhysics(projectId, which, version);
  if (error) return <section className="panel"><h2>Around the model: runtime and memory</h2><ErrorNote error={error} /></section>;
  if (!data) return <section className="panel"><h2>Around the model: runtime and memory</h2><div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div></section>;
  const rt = data.runtime;
  const q = rt.queue;
  let sentence: string;
  if (q.mode === "offline") sentence = `Offline processing: about ${num(q.seconds_per_1000_inputs ?? 0)} s per 1000 inputs, with no queueing.`;
  else if (q.stable === false) sentence = `The device would need to be busy ${(q.utilisation * 100).toFixed(0)}% of the time: inputs pile up. Lower the rate or speed up the setup.`;
  else sentence = `The device is busy ${(q.utilisation * 100).toFixed(0)}% of the time. From arrival to answer: about ${num(q.end_to_end_ms ?? rt.headline_latency_ms)} ms, against ${num(rt.headline_latency_ms)} ms for the model alone.`;
  return (
    <section className="panel" aria-labelledby="rm-h">
      <h2 id="rm-h">Around the model: runtime and memory</h2>
      <p className="tn-summary">{sentence}</p>
      <Level kind="explain" title="Runtime effects">
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Effect</th><th className="num">Time</th><th>Per</th><th>In the headline latency?</th><th>How it was obtained</th></tr></thead>
            <tbody>{rt.effects.map((e) => (
              <tr key={e.id}>
                <td>{e.title}<small className="block muted">{e.note}</small></td>
                <td className="num">{e.ms === null ? "unknown" : `${num(e.ms)} ms`}</td>
                <td>{e.per}</td>
                <td>{e.in_headline ? "✓ yes" : "✕ no, add it"}</td>
                <td>{e.provenance}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="hint">{rt.note}</p>
      </Level>
      <Level kind="engineering" title="Memory hierarchy">
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Level</th><th className="num">Capacity</th><th className="num">Bandwidth</th><th>Holds</th><th>Fits?</th><th className="num">Traffic per batch</th><th className="num">Time at bandwidth</th><th>Source</th></tr></thead>
            <tbody>{data.memory.map((m) => (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td className="num">{m.capacity_bytes !== null ? bytes(m.capacity_bytes) : "–"}</td>
                <td className="num">{m.bandwidth_gbs !== null ? `${num(m.bandwidth_gbs)} GB/s` : "–"}</td>
                <td>{m.holds}</td>
                <td>{m.fits === null ? "–" : m.fits ? "✓ yes" : "✕ no"}{m.need_bytes ? <small className="block muted">needs {bytes(m.need_bytes)}</small> : null}</td>
                <td className="num">{m.traffic_bytes !== null ? bytes(m.traffic_bytes) : "–"}</td>
                <td className="num">{m.time_ms !== null ? `${num(m.time_ms)} ms` : "–"}</td>
                <td>{m.source}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Level>
    </section>
  );
}

const PROV_TEXT: Record<string, string> = {
  measured: "Measured", calibrated: "Predicted, calibrated", analytical: "Predicted (physics model)", vendor_spec: "Vendor figure",
  assumed: "Assumed", surrogate: "Surrogate model", simulated: "Simulated", extracted_unverified: "Read from a file, unconfirmed",
  synthetic_example: "Synthetic example",
};
const METRIC_LABEL: Record<string, string> = {
  latency: "Latency per input", batch_time: "Batch time", capacity_fps: "Throughput", power_avg: "Average power", power_peak: "Peak power",
  energy_per_inference: "Energy per input", memory: "Working memory", temperature: "Temperature (with throttling)",
  temperature_unthrottled: "Temperature without throttling", thermal_headroom: "Thermal headroom", clock_factor: "Clock after throttling",
  accuracy: "Accuracy kept", cost_per_day: "Electricity cost per day", carbon_per_day: "Emissions per day",
};
const GRADE: Record<string, string> = { none: "○ none", "too few": "◔ too few", rejected: "✕ rejected", fair: "◑ fair", good: "◕ good", strong: "● strong" };

/** Source evidence for every headline value, and how good the device's calibration is. */
export function EvidencePanel({ projectId, version }: { projectId: number; version: number }) {
  const [ev, setEv] = useState<Evidence | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [setup, setSetup] = useState(0);
  useEffect(() => { physics.evidence(projectId).then(setEv).catch(setError); }, [projectId, version]);
  if (error) return <section className="panel"><h2>Evidence for every number</h2><ErrorNote error={error} /></section>;
  if (!ev) return <section className="panel"><h2>Evidence for every number</h2><div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div></section>;
  const s = ev.setups[setup];
  const cq = ev.calibration;
  const anyCal = cq.precisions.some((p) => p.applied);
  return (
    <section className="panel evidence" aria-labelledby="ev-h">
      <h2 id="ev-h">Evidence for every number</h2>
      <p className="tn-summary">
        {ev.device.name}: {ev.device.verified ? "a measured profile" : "figures from datasheets and stated assumptions"};
        {" "}{anyCal ? "a calibration from real runs is applied." : "no calibration from real runs is applied yet."}
      </p>
      {ev.message && <p className="hint">{ev.message}</p>}
      {s && (
        <>
          {ev.setups.length > 1 && (
            <div className="seg" role="radiogroup" aria-label="Setup">
              {ev.setups.map((x, i) => (
                <button key={x.label} role="radio" aria-checked={i === setup} className={i === setup ? "on" : ""} onClick={() => setSetup(i)}>{x.label}</button>
              ))}
            </div>
          )}
          <ul className="evidence-list">
            {s.metrics.map((m) => (
              <li key={m.key}>
                <details>
                  <summary>
                    <span className="ev-name">{METRIC_LABEL[m.key] ?? m.key}</span>
                    <span className="num ev-val">{num(m.value)} {m.unit}</span>
                    <span className="num muted ev-band">{num(m.low)} – {num(m.high)}</span>
                    <span className={`tag ${m.provenance}`}>{PROV_TEXT[m.provenance] ?? m.provenance}{m.calibrated ? ` · calibration v${m.calibration_version}` : ""}</span>
                  </summary>
                  <div className="ev-body">
                    <p>Range {num(m.low)} to {num(m.high)} {m.unit}: the best and worst case of the engine's uncertainty for this setup.</p>
                    {m.facts.length > 0 && (
                      <table className="tbl">
                        <thead><tr><th>Based on</th><th>Where that came from</th></tr></thead>
                        <tbody>{m.facts.map((f) => <tr key={f.fact}><td>{f.fact}</td><td>{f.source.replace("_", " ")}</td></tr>)}</tbody>
                      </table>
                    )}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
      <Level kind="engineering" title="Calibration quality for this device">
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Precision</th><th>Applied</th><th>Latency fit</th><th>Power fit</th><th className="num">Runs</th><th>Batches</th><th className="num">With temperature</th></tr></thead>
            <tbody>{cq.precisions.map((p) => (
              <tr key={p.precision}>
                <td>{p.precision.toUpperCase()}</td>
                <td>{p.applied ? `✓ v${p.version}` : "–"}</td>
                <td>{GRADE[p.latency.grade] ?? p.latency.grade}<small className="block muted">{p.latency.why}</small></td>
                <td>{GRADE[p.power.grade] ?? p.power.grade}<small className="block muted">{p.power.why}</small></td>
                <td className="num">{p.measurements}</td>
                <td>{p.batches.join(", ") || "–"}</td>
                <td className="num">{p.with_temperature}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="hint">{cq.note} Add runs under “Verify, history, export”.</p>
      </Level>
    </section>
  );
}
