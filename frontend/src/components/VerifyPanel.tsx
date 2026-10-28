import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type {
  BenchmarkStatus,
  CalibrationHistoryItem,
  HardwareProfile,
  ModelProfile,
  VerifyOut,
} from "../types";
import NebiusJobsPanel from "./NebiusJobsPanel";
import PredVsMeasuredChart from "./PredVsMeasuredChart";
import { fmt } from "./common";

const pct = (v: number | null) => (v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const signed = (v: number | null) => (v === null ? "–" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`);

export default function VerifyPanel({
  devices,
  models,
  onChanged,
}: {
  devices: HardwareProfile[];
  models: ModelProfile[];
  onChanged: () => void;
}) {
  const [data, setData] = useState<VerifyOut | null>(null);
  const [history, setHistory] = useState<CalibrationHistoryItem[]>([]);
  const [status, setStatus] = useState<BenchmarkStatus | null>(null);
  const [deviceFilter, setDeviceFilter] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [v, c, s] = await Promise.all([api.verify(deviceFilter || undefined), api.calibration(), api.benchmarkStatus()]);
      setData(v);
      setHistory(c.history);
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [deviceFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const [form, setForm] = useState({
    device_id: devices[0]?.id ?? "",
    model_id: models[0]?.id ?? "",
    precision: "fp16",
    batch: "1",
    latency_ms_mean: "",
    power_w_avg: "",
    temperature_c_max: "",
    accuracy_retention: "",
  });
  const [importText, setImportText] = useState("");
  const [importFormat, setImportFormat] = useState("bench_json");

  const num = (s: string) => (s === "" ? null : Number(s));

  async function submitManual() {
    setErr(null);
    setMsg(null);
    try {
      const out = await api.addMeasurement({
        device_id: form.device_id,
        model_id: form.model_id,
        precision: form.precision,
        batch: Number(form.batch) || 1,
        latency_ms_mean: num(form.latency_ms_mean),
        power_w_avg: num(form.power_w_avg),
        temperature_c_max: num(form.temperature_c_max),
        accuracy_retention: num(form.accuracy_retention),
        source: "manual",
      });
      setMsg(`Stored ${out.created} measurement. Calibration refitted.`);
      await load();
      onChanged();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function submitImport() {
    setErr(null);
    setMsg(null);
    try {
      const params: Record<string, string> = {};
      if (["nvidia_smi_csv", "tegrastats"].includes(importFormat)) {
        params.device_id = form.device_id;
        params.model_id = form.model_id;
        params.precision = form.precision;
        params.batch = form.batch || "1";
        if (form.latency_ms_mean) params.latency_ms_mean = form.latency_ms_mean;
      }
      const out = await api.importMeasurements(importFormat, importText, params);
      setMsg(`Imported ${out.created} new measurement(s); ${out.skipped_duplicates} duplicate(s) skipped.`);
      setImportText("");
      await load();
      onChanged();
    } catch (e) {
      setErr(String(e));
    }
  }

  const rows = data?.rows ?? [];
  const hist = history.slice(0, 8);

  return (
    <>
      <section className="panel">
        <div className="row">
          <h2>Verify: predicted vs measured</h2>
          <label className="field inline">
            <span>Device</span>
            <select value={deviceFilter} onChange={(e) => setDeviceFilter(e.target.value)}>
              <option value="">All with measurements</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
        </div>
        {status && (
          <p className="note-info">
            Nebius Serverless Jobs: <strong>{status.jobs_configured ? "configured" : "not configured"}</strong> — {status.detail}.
            Measurements enter by import or manual entry; the committed examples were run on the development machine&apos;s CPU, not a GPU.
          </p>
        )}
        {!rows.length && <p className="hint">No latency measurements for this selection yet.</p>}
        {data?.summaries.map((s) => (
          <div key={`${s.device_id}/${s.precision}`} className="verify-card">
            <div className="row">
              <strong>{devices.find((d) => d.id === s.device_id)?.name ?? s.device_id} · {s.precision.toUpperCase()}</strong>
              <span className={s.calibration_active ? "chip chip-predicted_feasible" : "chip chip-uncertain"}>
                {s.calibration_active ? `Calibration v${s.calibration_version} applied` : "Not calibrated"}
              </span>
            </div>
            <div className="facts">
              <div><span>Measurements</span><strong>{s.n}</strong></div>
              <div><span>Error before</span><strong>{pct(s.mape_before)}</strong></div>
              <div><span>Error after (held out)</span><strong>{pct(s.mape_after)}</strong></div>
              <div><span>Inside band</span><strong>{pct(s.coverage)}</strong></div>
            </div>
            <p className="hint">{s.note}</p>
          </div>
        ))}
        <PredVsMeasuredChart rows={rows} />
        {rows.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Model</th>
                <th>Batch</th>
                <th>Measured</th>
                <th>Predicted</th>
                <th>Calibrated (held out)</th>
                <th>In band</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.measurement.id}>
                  <td>{r.measurement.model_id}</td>
                  <td>{r.measurement.batch}</td>
                  <td><strong>{fmt(r.measurement.latency_ms_mean ?? 0)} ms</strong></td>
                  <td>{fmt(r.predicted_ms)} ms <span className="muted">({signed(r.error_before)})</span></td>
                  <td>
                    {r.predicted_cal_ms === null ? "–" : <>{fmt(r.predicted_cal_ms)} ms <span className="muted">({signed(r.error_after)})</span></>}
                  </td>
                  <td>{r.inside_band === null ? "–" : r.inside_band ? "yes" : "no"}</td>
                  <td><span className="badge badge-measured">{r.measurement.source.replace("_", " ")}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="hint">
          Calibration is only accepted if it predicts held-out points better than the uncalibrated model. It applies to the device and precision it was measured on, and never to other hardware.
        </p>
      </section>

      {hist.length > 0 && (
        <section className="panel">
          <h2>Calibration versions</h2>
          <table className="tbl">
            <thead>
              <tr><th>Device</th><th>Version</th><th>Result</th><th>Latency error before → after</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {hist.map((h) => (
                <tr key={`${h.device_id}${h.precision}${h.version}`}>
                  <td>{h.device_id} · {h.precision}</td>
                  <td>v{h.version}</td>
                  <td><span className={h.accepted ? "chip chip-predicted_feasible" : "chip chip-infeasible"}>{h.accepted ? "accepted" : "rejected"}</span></td>
                  <td>{pct(h.latency.error_before)} → {pct(h.latency.error_after)}</td>
                  <td className="muted">{h.latency.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <NebiusJobsPanel devices={devices} onIngested={() => { load(); onChanged(); }} />

      <section className="panel">
        <h2>Add measurements</h2>
        <details>
          <summary>Enter one measurement</summary>
          <div className="grid2">
            <label className="field"><span>Device</span>
              <select value={form.device_id} onChange={(e) => setForm({ ...form, device_id: e.target.value })}>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select></label>
            <label className="field"><span>Model</span>
              <select value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })}>
                {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select></label>
            <label className="field"><span>Precision</span>
              <select value={form.precision} onChange={(e) => setForm({ ...form, precision: e.target.value })}>
                <option value="fp32">FP32</option><option value="fp16">FP16</option><option value="int8">INT8</option>
              </select></label>
            <label className="field"><span>Batch</span><input type="number" min={1} value={form.batch} onChange={(e) => setForm({ ...form, batch: e.target.value })} /></label>
            <label className="field"><span>Mean batch latency (ms)</span><input type="number" value={form.latency_ms_mean} onChange={(e) => setForm({ ...form, latency_ms_mean: e.target.value })} /></label>
            <label className="field"><span>Average power under load (W)</span><input type="number" value={form.power_w_avg} onChange={(e) => setForm({ ...form, power_w_avg: e.target.value })} /></label>
            <label className="field"><span>Max temperature (°C)</span><input type="number" value={form.temperature_c_max} onChange={(e) => setForm({ ...form, temperature_c_max: e.target.value })} /></label>
            <label className="field"><span>Accuracy retained vs FP32 (0–1)</span><input type="number" step={0.01} value={form.accuracy_retention} onChange={(e) => setForm({ ...form, accuracy_retention: e.target.value })} /></label>
          </div>
          <p className="hint">Enter only what you measured; empty fields stay empty. A measured accuracy retention lets that precision be certified instead of &ldquo;uncertain&rdquo;.</p>
          <button onClick={submitManual}>Add measurement</button>
        </details>
        <details>
          <summary>Import a result file or power trace</summary>
          <label className="field"><span>Format</span>
            <select value={importFormat} onChange={(e) => setImportFormat(e.target.value)}>
              <option value="bench_json">bench/run_benchmark.py JSON</option>
              <option value="json">JSON list of measurements</option>
              <option value="csv">CSV, one measurement per row (device_id, model_id, precision, batch, latency_ms, power_w...)</option>
              <option value="nvidia_smi_csv">nvidia-smi CSV power trace</option>
              <option value="tegrastats">tegrastats log (Jetson)</option>
            </select></label>
          {["nvidia_smi_csv", "tegrastats"].includes(importFormat) && <p className="hint">Power traces use the device, model, precision, batch and latency from the form above.</p>}
          <textarea rows={6} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder="Paste the file contents here" />
          <button disabled={!importText.trim()} onClick={submitImport}>Import</button>
        </details>
        {msg && <p className="note-info">{msg}</p>}
        {err && <p className="error">{err}</p>}
      </section>
    </>
  );
}
