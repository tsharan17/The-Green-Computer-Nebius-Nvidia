import { useCallback, useEffect, useState } from "react";
import { type RemoteDevice, type RemoteJob, remote, workspace } from "../api";
import { when } from "../pages/WorkspacePage";
import { num } from "../twin/chart";
import type { ArtifactItem, HardwareProfile } from "../types";
import { ErrorNote, Level } from "./Help";

const DEV_STATUS: Record<string, { icon: string; cls: string; text: string }> = {
  online: { icon: "●", cls: "ok", text: "online" },
  offline: { icon: "○", cls: "none", text: "paired, not seen in the last 2 minutes" },
  pending: { icon: "◌", cls: "warn", text: "waiting to pair" },
  revoked: { icon: "✕", cls: "bad", text: "revoked" },
};
const JOB_STATUS: Record<string, { icon: string; cls: string }> = {
  queued: { icon: "◌", cls: "none" }, claimed: { icon: "◐", cls: "warn" }, succeeded: { icon: "✓", cls: "ok" },
  failed: { icon: "✕", cls: "bad" }, rejected: { icon: "✕", cls: "bad" }, expired: { icon: "○", cls: "none" },
};
const VERDICT: Record<string, { icon: string; cls: string }> = { good: { icon: "✓", cls: "ok" }, usable: { icon: "◑", cls: "warn" }, poor: { icon: "✕", cls: "bad" } };

/** Pair real devices and run approved ONNX files on them through the device agent. */
export default function DevicesPanel({ projectId, canWrite, devices, defaultDevice, onMeasured }: {
  projectId: number; canWrite: boolean; devices: HardwareProfile[]; defaultDevice: string; onMeasured: () => void;
}) {
  const [list, setList] = useState<RemoteDevice[] | null>(null);
  const [jobs, setJobs] = useState<RemoteJob[]>([]);
  const [files, setFiles] = useState<ArtifactItem[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [fresh, setFresh] = useState<RemoteDevice | null>(null);
  const [name, setName] = useState("");
  const [profile, setProfile] = useState(defaultDevice);
  const [run, setRun] = useState({ device: 0, artifact: 0, precision: "fp32", batch: 1, warmup: 10, runs: 100, max_seconds: 120 });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, j, f] = await Promise.all([remote.devices(), remote.jobs(projectId), workspace.artifacts(projectId)]);
      setList(d);
      setJobs((prev) => {
        if (j.some((x) => x.measurement_id && !prev.find((p) => p.id === x.id && p.measurement_id))) onMeasured();
        return j;
      });
      const onnx = f.filter((a) => a.kind === "onnx_model" && a.status === "valid" && a.stored);
      setFiles(onnx);
      setRun((r) => ({ ...r, device: r.device || d.find((x) => x.status === "paired")?.id || 0, artifact: r.artifact || onnx[0]?.id || 0 }));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [projectId, onMeasured]);
  useEffect(() => { load(); }, [load]);
  const active = jobs.some((j) => j.status === "queued" || j.status === "claimed") || (list ?? []).some((d) => d.status === "pending");
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [active, load]);

  async function act<T>(f: () => Promise<T>, after?: (v: T) => void) {
    setBusy(true);
    try {
      const v = await f();
      after?.(v);
      await load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const paired = (list ?? []).filter((d) => d.status === "paired");
  const server = window.location.origin;
  const file = files.find((f) => f.id === run.artifact);
  return (
    <div className="devices-panel">
      <section className="panel" aria-labelledby="dv-h">
        <h2 id="dv-h">Run on a real device</h2>
        <p className="tn-summary">Install the small agent on your device, pair it once, then approve runs of your ONNX model from here. Results come back as measurements, checked for quality first.</p>
        <p className="hint">The agent only makes outbound HTTPS requests, runs only signed requests for the approved file, and never runs a shell. The agent is the single file agent/gc_agent.py in this installation's repository; docs/remote-agent.md explains set-up and the protocol.</p>
        <ErrorNote error={error} onRetry={load} />

        {fresh?.pairing_code && (
          <div className="pairing-box" role="status">
            <strong>Pairing code for {fresh.name}: <code className="code-big">{fresh.pairing_code}</code></strong>
            <p>Shown once; works until {when(fresh.pairing_expires_at)}. On the device run:</p>
            <pre className="cmd">{`python gc_agent.py pair --server ${server} --code ${fresh.pairing_code}\npython gc_agent.py run`}</pre>
            <button className="link" onClick={() => setFresh(null)}>Done</button>
          </div>
        )}

        {list === null ? <div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div> : list.length === 0 ? (
          <p className="empty-state">No devices yet. Register one below.</p>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Device</th><th>Status</th><th>Agent</th><th>Last seen</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{list.map((d) => {
                const st = DEV_STATUS[d.status === "paired" ? (d.online ? "online" : "offline") : d.status];
                return (
                  <tr key={d.id}>
                    <td><strong>{d.name}</strong><small className="block muted">{devices.find((h) => h.id === d.device_id)?.name ?? d.device_id}</small></td>
                    <td><span className={`outcome ${st.cls}`}><span aria-hidden className="outcome-icon">{st.icon}</span> {st.text}</span></td>
                    <td className="muted">{d.agent_info.hostname ? `${d.agent_info.hostname}, ${d.agent_info.os ?? ""}, ONNX Runtime ${d.agent_info.onnxruntime ?? "?"}` : "–"}</td>
                    <td className="muted">{when(d.last_seen_at)}</td>
                    <td className="actions-cell">{canWrite && d.status !== "revoked" && (
                      <>
                        <button className="link" disabled={busy} onClick={() => act(() => remote.pairingCode(d.id), setFresh)}>New pairing code</button>
                        <button className="link" disabled={busy} onClick={() => window.confirm(`Revoke ${d.name}? Its token stops working at once.`) && act(() => remote.revoke(d.id))}>Revoke</button>
                      </>
                    )}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}

        {canWrite && (
          <form className="inline-form" onSubmit={(e) => { e.preventDefault(); act(() => remote.register(name || "My device", profile), (d) => { setFresh(d); setName(""); }); }}>
            <label className="field">Device name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Bench Orin Nano" maxLength={120} /></label>
            <label className="field">Hardware profile
              <select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {devices.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
            </label>
            <button className="primary" disabled={busy}>Register and get a pairing code</button>
          </form>
        )}
      </section>

      <section className="panel" aria-labelledby="rr-h">
        <h2 id="rr-h">Approve a run</h2>
        {paired.length === 0 ? <p className="hint">Pair a device first.</p> : files.length === 0 ? (
          <p className="hint">Upload the model as an ONNX file under “Model and hardware”; only validated ONNX files can be sent to a device.</p>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); act(() => remote.approve(run.device, { project_id: projectId, artifact_id: run.artifact, precision: run.precision, batch: run.batch, warmup: run.warmup, runs: run.runs, max_seconds: run.max_seconds })); }}>
            <div className="grid3">
              <label className="field">Device<select value={run.device} onChange={(e) => setRun({ ...run, device: Number(e.target.value) })}>{paired.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
              <label className="field">ONNX file<select value={run.artifact} onChange={(e) => setRun({ ...run, artifact: Number(e.target.value) })}>{files.map((f) => <option key={f.id} value={f.id}>{f.filename}</option>)}</select></label>
              <label className="field">Precision of this file<select value={run.precision} onChange={(e) => setRun({ ...run, precision: e.target.value })}><option value="fp32">FP32</option><option value="fp16">FP16</option><option value="int8">INT8</option></select></label>
              <label className="field">Batch<input type="number" min={1} max={64} value={run.batch} onChange={(e) => setRun({ ...run, batch: Number(e.target.value) || 1 })} /></label>
              <label className="field">Timed runs<input type="number" min={1} max={5000} value={run.runs} onChange={(e) => setRun({ ...run, runs: Number(e.target.value) || 1 })} /></label>
              <label className="field">Time limit (s)<input type="number" min={1} max={1800} value={run.max_seconds} onChange={(e) => setRun({ ...run, max_seconds: Number(e.target.value) || 1 })} /></label>
            </div>
            {file && <p className="hint">Approves one run of {file.filename} (SHA-256 {file.sha256.slice(0, 12)}…) on this device, after {run.warmup} warm-up runs. The request is signed and expires in an hour.</p>}
            {canWrite && <button className="primary" disabled={busy}>Approve and send</button>}
          </form>
        )}
      </section>

      <section className="panel" aria-labelledby="rj-h">
        <h2 id="rj-h">Runs on devices</h2>
        {jobs.length === 0 ? <p className="hint">No runs yet.</p> : (
          <ul className="job-list">
            {jobs.map((j) => {
              const st = JOB_STATUS[j.status];
              const v = j.quality ? VERDICT[j.quality.verdict] : null;
              return (
                <li key={j.id}>
                  <div className="row">
                    <span><span className={`outcome ${st.cls}`}><span aria-hidden className="outcome-icon">{st.icon}</span> {j.status}</span>
                      {j.mode && <span className={`tag ${j.mode === "simulated" ? "assumed" : "measured"}`}> {j.mode}</span>}</span>
                    <span className="muted">{j.artifact?.filename} · {(list ?? []).find((d) => d.id === j.remote_device_id)?.name} · {when(j.created_at)}</span>
                  </div>
                  {j.quality?.stats && <p className="num">Mean {num(j.quality.stats.mean)} ms, p95 {num(j.quality.stats.p95)} ms over {j.quality.stats.n} runs{j.measurement_id ? " · imported as a measurement" : ""}</p>}
                  {v && <p><span className={`outcome ${v.cls}`}><span aria-hidden className="outcome-icon">{v.icon}</span> quality {j.quality!.verdict}</span></p>}
                  {j.error && <p className="muted">{j.error}</p>}
                  {j.quality && (
                    <Level kind="engineering" title="Quality checks">
                      <ul className="checks-list">{j.quality.checks.map((c) => <li key={c.id}><span aria-hidden>{c.status === "pass" ? "✓" : c.status === "warn" ? "◑" : "✕"}</span> {c.detail}</li>)}</ul>
                      {j.environment && <p className="hint">{Object.entries(j.environment).map(([k, val]) => `${k}: ${val}`).join(" · ")}</p>}
                    </Level>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
