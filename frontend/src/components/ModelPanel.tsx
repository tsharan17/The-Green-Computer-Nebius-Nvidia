import { useRef, useState } from "react";
import { api } from "../api";
import type { ModelProfile } from "../types";

export default function ModelPanel({ onAdded }: { onAdded: (m: ModelProfile) => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dyn, setDyn] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState({ name: "", params_m: "", gflops: "", n_layers: "50" });

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const out = await api.uploadModel(file, { dynamicDim: dyn ? Number(dyn) : undefined });
      onAdded(out.profile);
      const p = out.profile;
      setMsg(
        `Parsed ${p.n_layers} layers: ${p.params_m.toFixed(2)} M params, ${p.gflops.toFixed(2)} GFLOPs.` +
          (out.warnings.length ? ` Notes: ${out.warnings.join("; ")}` : ""),
      );
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addManual() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const p = await api.manualModel({
        name: manual.name || "manual model",
        params_m: Number(manual.params_m),
        gflops: Number(manual.gflops),
        n_layers: Number(manual.n_layers) || 50,
      });
      onAdded(p);
      setMsg("Added. Manual parameters give wider uncertainty bands.");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="model-panel">
      <summary>Add your own model</summary>
      <h3>ONNX file</h3>
      <p className="hint">Only ONNX is accepted (no pickle or .pt). Weights are not needed for performance, power and heat.</p>
      <input ref={fileRef} type="file" accept=".onnx" />
      <label className="field">
        <span>Default size for dynamic dimensions (optional)</span>
        <input type="number" min={1} value={dyn} placeholder="e.g. 224" onChange={(e) => setDyn(e.target.value)} />
      </label>
      <button disabled={busy} onClick={upload}>Upload and parse</button>

      <h3>Or enter parameters</h3>
      <div className="grid2">
        <label className="field"><span>Name</span><input value={manual.name} onChange={(e) => setManual({ ...manual, name: e.target.value })} /></label>
        <label className="field"><span>Parameters (M)</span><input type="number" value={manual.params_m} onChange={(e) => setManual({ ...manual, params_m: e.target.value })} /></label>
        <label className="field"><span>GFLOPs per sample</span><input type="number" value={manual.gflops} onChange={(e) => setManual({ ...manual, gflops: e.target.value })} /></label>
        <label className="field"><span>Layers</span><input type="number" value={manual.n_layers} onChange={(e) => setManual({ ...manual, n_layers: e.target.value })} /></label>
      </div>
      <button disabled={busy || !manual.params_m || !manual.gflops} onClick={addManual}>Add model</button>
      {msg && <p className="note-info">{msg}</p>}
      {err && <p className="error">{err}</p>}
    </details>
  );
}
