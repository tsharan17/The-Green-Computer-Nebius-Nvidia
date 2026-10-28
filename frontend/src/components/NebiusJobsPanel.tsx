import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { BenchmarkJob, BenchmarkStatus, HardwareProfile } from "../types";

const GPU_DEVICES = ["nebius-l40s", "nebius-h100"];

export default function NebiusJobsPanel({
  devices,
  onIngested,
}: {
  devices: HardwareProfile[];
  onIngested: () => void;
}) {
  const [status, setStatus] = useState<BenchmarkStatus | null>(null);
  const [jobs, setJobs] = useState<BenchmarkJob[]>([]);
  const [form, setForm] = useState({ device_id: GPU_DEVICES[0], model_id: "resnet-50", precision: "fp32", batches: "1,8,32" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, j] = await Promise.all([api.benchmarkStatus(), api.benchmarks()]);
      setStatus(s);
      setJobs(j);
    } catch (e) {
      setErr(String(e));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function launch() {
    setBusy(true);
    setErr(null);
    try {
      const batches = form.batches.split(",").map((x) => Number(x.trim())).filter((n) => n >= 1);
      await api.submitBenchmark({ ...form, batches });
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function refresh(j: BenchmarkJob) {
    setErr(null);
    try {
      const upd = await api.refreshBenchmark(j.id);
      setJobs((all) => all.map((x) => (x.id === j.id ? upd : x)));
      if (upd.ingested && !j.ingested) onIngested();
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <section className="panel">
      <h2>Real GPU benchmarks on Nebius</h2>
      <p className="hint">
        Launches a Serverless Job on an NVIDIA GPU that runs a reference model with ONNX Runtime and samples
        <code> nvidia-smi</code> power and temperature, then imports the result here. Reference models only.
      </p>
      {status && (
        <p className={status.jobs_configured ? "note-info" : "note"}>
          {status.jobs_configured ? "Ready: " : "Not available: "}
          {status.detail}
          {!status.jobs_configured && " You can still run bench/ on any GPU host and import the JSON below."}
        </p>
      )}
      <div className="grid2">
        <label className="field"><span>GPU</span>
          <select value={form.device_id} onChange={(e) => setForm({ ...form, device_id: e.target.value })}>
            {devices.filter((d) => GPU_DEVICES.includes(d.id)).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select></label>
        <label className="field"><span>Model</span>
          <select value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })}>
            <option value="resnet-50">ResNet-50</option><option value="mobilenet-v2">MobileNetV2</option>
          </select></label>
        <label className="field"><span>Precision</span>
          <select value={form.precision} onChange={(e) => setForm({ ...form, precision: e.target.value })}>
            <option value="fp32">FP32</option><option value="fp16">FP16</option>
          </select></label>
        <label className="field"><span>Batch sizes</span>
          <input value={form.batches} onChange={(e) => setForm({ ...form, batches: e.target.value })} /></label>
      </div>
      <button className="primary" disabled={busy || !status?.jobs_configured} onClick={launch}>
        {busy ? "Launching…" : "Launch GPU benchmark (billed)"}
      </button>
      {err && <p className="error">{err}</p>}
      {jobs.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Job</th><th>GPU · model</th><th>State</th><th>Imported</th><th /></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.job_name}</td>
                <td>{j.device_id} · {j.model_id} · {j.precision} · b{j.batches.join(",")}</td>
                <td><span className={`chip ${j.state === "succeeded" ? "chip-predicted_feasible" : j.state === "failed" ? "chip-infeasible" : "chip-uncertain"}`}>{j.state}</span></td>
                <td>{j.ingested ? `${j.measurement_ids.length} measurements` : j.error ?? "–"}</td>
                <td><button onClick={() => refresh(j)}>Refresh</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
