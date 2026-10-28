import { useCallback, useEffect, useState } from "react";
import AiBadge from "./AiBadge";
import { api } from "../api";
import type { HardwareDraft } from "../types";

function DraftEditor({
  draft,
  onDone,
}: {
  draft: HardwareDraft;
  onDone: (confirmedName?: string) => void;
}) {
  const c = draft.candidate;
  const [f, setF] = useState({
    name: c.name ?? draft.query,
    fp32: String(c.peak_tflops?.fp32 ?? ""),
    fp16: String(c.peak_tflops?.fp16 ?? ""),
    int8: String(c.peak_tflops?.int8 ?? ""),
    bw: String(c.mem_bw_gbs ?? ""),
    mem: String(c.mem_gb ?? ""),
    modes: (c.power_modes ?? []).map((m) => `${m.name}:${m.cap_w}`).join(", "),
    tdp: String(c.tdp_w ?? ""),
    thr: String(c.t_throttle_c ?? ""),
  });
  const [err, setErr] = useState<string | null>(null);
  const src = (field: string) => draft.evidence.find((e) => e.field === field)?.source_url;

  async function confirm() {
    setErr(null);
    const peaks: Record<string, number> = {};
    (["fp32", "fp16", "int8"] as const).forEach((p) => {
      if (f[p]) peaks[p] = Number(f[p]);
    });
    const modes = f.modes
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const [name, cap] = s.split(":");
        return { name: name.trim(), cap_w: Number(cap) };
      })
      .filter((m) => m.name && m.cap_w > 0);
    try {
      await api.confirmDraft(draft.id, {
        name: f.name,
        peak_tflops: peaks,
        mem_bw_gbs: Number(f.bw),
        mem_gb: Number(f.mem),
        power_modes: modes,
        ...(f.tdp ? { tdp_w: Number(f.tdp) } : {}),
        ...(f.thr ? { t_throttle_c: Number(f.thr) } : {}),
      });
      onDone(f.name);
    } catch (e) {
      setErr(String(e));
    }
  }

  const missing = new Set(draft.missing);
  return (
    <div className="draft">
      <p className="note">
        Review every value before using this device. These were read from the web by an AI and are{" "}
        <strong>unverified</strong>; power and cooling are filled with assumptions.
      </p>
      <div className="grid2">
        <label className="field"><span>Name</span><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field"><span>Memory (GB){missing.has("mem_gb") ? " · missing" : ""}</span><input type="number" value={f.mem} onChange={(e) => setF({ ...f, mem: e.target.value })} /></label>
        <label className="field"><span>Memory bandwidth (GB/s){missing.has("mem_bw_gbs") ? " · missing" : ""}</span><input type="number" value={f.bw} onChange={(e) => setF({ ...f, bw: e.target.value })} /></label>
        <label className="field"><span>FP32 TFLOPS</span><input type="number" value={f.fp32} onChange={(e) => setF({ ...f, fp32: e.target.value })} /></label>
        <label className="field"><span>FP16 TFLOPS</span><input type="number" value={f.fp16} onChange={(e) => setF({ ...f, fp16: e.target.value })} /></label>
        <label className="field"><span>INT8 TOPS (dense)</span><input type="number" value={f.int8} onChange={(e) => setF({ ...f, int8: e.target.value })} /></label>
        <label className="field"><span>Power modes (name:watts, …)</span><input value={f.modes} placeholder="15W:15, 30W:30" onChange={(e) => setF({ ...f, modes: e.target.value })} /></label>
        <label className="field"><span>Or just max power (W)</span><input type="number" value={f.tdp} onChange={(e) => setF({ ...f, tdp: e.target.value })} /></label>
        <label className="field"><span>Throttle temperature (°C)</span><input type="number" value={f.thr} placeholder="default 90" onChange={(e) => setF({ ...f, thr: e.target.value })} /></label>
      </div>
      {draft.evidence.filter((e) => e.source_url).length > 0 && (
        <p className="hint">
          Sources:{" "}
          {[...new Set(draft.evidence.map((e) => e.source_url).filter(Boolean))].map((u) => (
            <a key={u} href={u as string} target="_blank" rel="noreferrer">{(u as string).replace(/^https?:\/\//, "").slice(0, 48)} </a>
          ))}
          {["mem_bw_gbs", "mem_gb", "peak_tflops"].every((k) => src(k)) ? "" : " (some values have no source)"}
        </p>
      )}
      {draft.dropped_implausible && draft.dropped_implausible.length > 0 && (
        <p className="hint">Dropped as implausible: {draft.dropped_implausible.join(", ")}.</p>
      )}
      <div className="cta-row">
        <button className="primary" onClick={confirm}>Confirm and use this device</button>
        <button onClick={async () => { await api.rejectDraft(draft.id); onDone(); }}>Discard</button>
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

export default function HardwareImportCard({ onConfirmed, refreshKey = 0 }: { onConfirmed: () => void; refreshKey?: number }) {
  const [name, setName] = useState("");
  const [drafts, setDrafts] = useState<HardwareDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [manual, setManual] = useState({ name: "", fp16: "", bw: "", mem: "", tdp: "" });

  const load = useCallback(async () => {
    try {
      setDrafts((await api.drafts()).filter((d) => d.status === "draft"));
    } catch {
      /* the card still works without the list */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function search() {
    setBusy(true);
    setErr(null);
    try {
      await api.importHardware(name);
      setName("");
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addManual() {
    setErr(null);
    try {
      await api.manualHardware({
        name: manual.name,
        peak_tflops: { fp16: Number(manual.fp16) },
        mem_bw_gbs: Number(manual.bw),
        mem_gb: Number(manual.mem),
        ...(manual.tdp ? { tdp_w: Number(manual.tdp) } : {}),
      });
      await load();
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <details className="import-card">
      <summary>
        Your hardware isn't in the list? Add it <AiBadge compact role="reads the datasheet, you confirm" extra="Tavily" />
      </summary>
      <p className="hint">
        Type its name and we search for its datasheet and read the specs for you (needs the Tavily and Nemotron keys), or
        enter the numbers yourself. You always confirm the values before the device is used. Searching sends the name you type to
        Tavily and the public pages it finds to the AI provider; none of your files are sent.
      </p>
      <div className="cta-row">
        <input value={name} placeholder="e.g. Jetson Orin NX 8GB" onChange={(e) => setName(e.target.value)} />
        <button disabled={busy || name.trim().length < 2} onClick={search}>
          {busy ? "Searching…" : "Find its specs"}
        </button>
      </div>
      <details>
        <summary className="muted">Enter specs by hand instead</summary>
        <div className="grid2">
          <label className="field"><span>Name</span><input value={manual.name} onChange={(e) => setManual({ ...manual, name: e.target.value })} /></label>
          <label className="field"><span>FP16 TFLOPS</span><input type="number" value={manual.fp16} onChange={(e) => setManual({ ...manual, fp16: e.target.value })} /></label>
          <label className="field"><span>Memory bandwidth (GB/s)</span><input type="number" value={manual.bw} onChange={(e) => setManual({ ...manual, bw: e.target.value })} /></label>
          <label className="field"><span>Memory (GB)</span><input type="number" value={manual.mem} onChange={(e) => setManual({ ...manual, mem: e.target.value })} /></label>
          <label className="field"><span>Max power (W)</span><input type="number" value={manual.tdp} onChange={(e) => setManual({ ...manual, tdp: e.target.value })} /></label>
        </div>
        <button disabled={!manual.name || !manual.fp16 || !manual.bw || !manual.mem || !manual.tdp} onClick={addManual}>Create draft</button>
      </details>
      {err && <p className="error">{err}</p>}
      {drafts.map((d) => (
        <DraftEditor
          key={d.id}
          draft={d}
          onDone={async (confirmed) => {
            await load();
            if (confirmed) onConfirmed();
          }}
        />
      ))}
    </details>
  );
}
