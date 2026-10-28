import { useEffect, useMemo, useState } from "react";
import { guide, intake, type UseCase } from "../api";
import { ErrorNote } from "../components/Help";
import { useMode } from "../lib/mode";
import { navigate } from "../router";
import type { HardwareProfile, ModelProfile } from "../types";

const STEPS = ["What you are building", "Your AI model", "Your hardware", "What matters most", "Analyse"];
const KIND_LABEL: Record<string, string> = {
  edge_soc: "Edge computers", sbc: "Single-board computers", mcu: "Microcontrollers", fpga: "FPGAs",
  accelerator: "AI accelerators", datacenter_gpu: "Data-centre GPUs", laptop_soc: "PC and laptop chips", custom: "Your hardware",
};

export default function StartGuide({
  models,
  devices,
  onModelAdded,
}: {
  models: ModelProfile[];
  devices: HardwareProfile[];
  onModelAdded: (m: ModelProfile) => void;
}) {
  const { setOnboarded } = useMode();
  const [step, setStep] = useState(0);
  const [cases, setCases] = useState<UseCase[]>([]);
  const [priorities, setPriorities] = useState<{ id: string; title: string; blurb: string }[]>([]);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("mobilenet-v2");
  const [deviceId, setDeviceId] = useState("jetson-orin-nano");
  const [priority, setPriority] = useState("balanced");
  const [fps, setFps] = useState(30);
  const [power, setPower] = useState(15);
  const [ambient, setAmbient] = useState(25);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    guide.useCases().then((r) => { setCases(r.use_cases); setPriorities(r.priorities); }).catch(setError);
  }, []);

  function pickCase(c: UseCase | null) {
    setCaseId(c?.id ?? null);
    if (c) {
      setModelId(c.model_id);
      setDeviceId(c.device_id);
      setPriority(c.priority);
      setFps(c.fps);
      setPower(c.max_power_w);
      setAmbient(c.ambient_c);
      setName(c.title);
    } else setName("My project");
    setStep(1);
  }

  async function upload(file: File) {
    setUploadMsg("Checking the file…");
    try {
      const r = await intake.upload(file, { category: "model" });
      if (r.status === "valid" && r.model) {
        onModelAdded(r.model);
        setModelId(r.model.id);
        setUploadMsg(`Added ${r.model.name}: ${r.message}`);
      } else setUploadMsg(`${r.message}${r.required ? ` What works: ${r.required}` : ""}`);
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function analyse() {
    setBusy(true);
    setError(null);
    try {
      const r = await guide.start({ use_case: caseId, name, model_id: modelId, device_id: deviceId, priority, fps,
        max_power_w: power, ambient_c: ambient, analyse: true });
      setOnboarded(true);
      navigate(`/p/${r.project.id}/summary`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const grouped = useMemo(() => {
    const m = new Map<string, HardwareProfile[]>();
    for (const d of devices) m.set(d.kind, [...(m.get(d.kind) ?? []), d]);
    return [...m.entries()];
  }, [devices]);
  const model = models.find((m) => m.id === modelId);
  const device = devices.find((d) => d.id === deviceId);
  const uc = cases.find((c) => c.id === caseId);

  return (
    <div className="guide">
      <header className="guide-head">
        <h1>Start a new analysis</h1>
        <p className="muted">Five short steps. You get a plain verdict, the biggest bottleneck and one recommended change, with every engineering detail one click away.</p>
      </header>
      <ol className="guide-steps" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "on" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
            <span className="gs-n" aria-hidden>{i < step ? "✓" : i + 1}</span> {s}
          </li>
        ))}
      </ol>
      <ErrorNote error={error} onRetry={step === 4 ? analyse : undefined} />

      {step === 0 && (
        <section aria-labelledby="g0">
          <h2 id="g0">What are you trying to build?</h2>
          <p className="muted">Pick the closest example; it fills in sensible starting values you can change in the next steps.</p>
          <div className="choice-grid">
            {cases.map((c) => (
              <button key={c.id} className="choice" onClick={() => pickCase(c)}>
                <strong>{c.title}</strong>
                <span>{c.blurb}</span>
              </button>
            ))}
            <button className="choice custom" onClick={() => pickCase(null)}>
              <strong>Something else</strong>
              <span>Start from scratch and choose every part yourself.</span>
            </button>
          </div>
        </section>
      )}

      {step === 1 && (
        <section aria-labelledby="g1">
          <h2 id="g1">Add your AI model</h2>
          <fieldset className="radio-list">
            <legend className="sr-only">Model</legend>
            <label className="field">Choose a model
              <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                {models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.fidelity === "library" ? m.task : "yours"})</option>)}
              </select>
            </label>
            {model && <p className="hint">{model.name}: {model.task}, {(model.params_m).toFixed(1)} million parameters, {(model.gflops).toFixed(2)} billion operations per input.{uc?.model_id === model.id ? " Suggested for this use case." : ""}</p>}
            <div className="upload-line">
              <span>Or upload your own ONNX file:</span>
              <input type="file" accept=".onnx" aria-label="Upload an ONNX model" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </div>
            {uploadMsg && <p className="note-info" role="status">{uploadMsg}</p>}
            <p className="hint">PyTorch or TensorFlow model? Export it to ONNX first (torch.onnx.export or tf2onnx).</p>
          </fieldset>
          <Nav back={() => setStep(0)} next={() => setStep(2)} />
        </section>
      )}

      {step === 2 && (
        <section aria-labelledby="g2">
          <h2 id="g2">Describe your hardware</h2>
          <label className="field">Choose a device
            <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
              {grouped.map(([kind, ds]) => (
                <optgroup key={kind} label={KIND_LABEL[kind] ?? kind}>
                  {ds.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          {device && <p className="hint">{device.name}: {device.role || device.kind.replace("_", " ")}. {device.verified ? "Measured profile." : "Figures from datasheets and stated assumptions."}</p>}
          <p className="hint">Your own board, microcontroller or FPGA? Pick the closest one now; after the first result you can describe it exactly or upload its profile, reports and logs under “Model and hardware”.</p>
          <Nav back={() => setStep(1)} next={() => setStep(3)} />
        </section>
      )}

      {step === 3 && (
        <section aria-labelledby="g3">
          <h2 id="g3">What matters most?</h2>
          <p className="muted">This decides which setup we recommend when several meet your requirements. It never loosens a requirement.</p>
          <div className="choice-grid compact" role="radiogroup" aria-label="Priority">
            {priorities.map((p) => (
              <button key={p.id} role="radio" aria-checked={priority === p.id} className={`choice ${priority === p.id ? "on" : ""}`} onClick={() => setPriority(p.id)}>
                <strong>{p.title}</strong>
                <span>{p.blurb}</span>
              </button>
            ))}
          </div>
          <h3>Three numbers that must hold</h3>
          <div className="grid3">
            <label className="field">Inputs per second it must handle<input type="number" min={0.1} value={fps} onChange={(e) => setFps(Number(e.target.value) || 1)} /></label>
            <label className="field">Power limit (watts)<input type="number" min={0.01} step={0.1} value={power} onChange={(e) => setPower(Number(e.target.value) || 1)} /></label>
            <label className="field">Room temperature (°C)<input type="number" value={ambient} onChange={(e) => setAmbient(Number(e.target.value))} /></label>
          </div>
          <p className="hint">Latency, accuracy, memory and cooling can be set precisely later under “Constraints and environment”.</p>
          <Nav back={() => setStep(2)} next={() => setStep(4)} />
        </section>
      )}

      {step === 4 && (
        <section aria-labelledby="g4">
          <h2 id="g4">Ready to analyse</h2>
          <label className="field">Project name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></label>
          <dl className="kv">
            <dt>Model</dt><dd>{model?.name ?? modelId}</dd>
            <dt>Hardware</dt><dd>{device?.name ?? deviceId}</dd>
            <dt>Must handle</dt><dd>{fps} inputs per second, at most {power} W, in a {ambient} °C room</dd>
            <dt>Priority</dt><dd>{priorities.find((p) => p.id === priority)?.title}</dd>
          </dl>
          <p className="hint">The analysis predicts speed, memory, power, heat and energy for thousands of settings in a few seconds. Results are predictions with ranges, labelled as such.</p>
          <div className="actions">
            <button onClick={() => setStep(3)}>Back</button>
            <button className="primary" disabled={busy} onClick={analyse}>{busy ? "Analysing…" : "Analyse"}</button>
          </div>
        </section>
      )}
    </div>
  );
}

function Nav({ back, next }: { back: () => void; next: () => void }) {
  return (
    <div className="actions">
      <button onClick={back}>Back</button>
      <button className="primary" onClick={next}>Continue</button>
    </div>
  );
}
