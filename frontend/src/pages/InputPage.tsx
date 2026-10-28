import { useState } from "react";
import CustomHardwareWizard from "../components/CustomHardwareWizard";
import DevicePicker from "../components/DevicePicker";
import HardwareImportCard from "../components/HardwareImportCard";
import IntakePanel from "../components/IntakePanel";
import ModelPanel from "../components/ModelPanel";
import type { HardwareProfile, ModelProfile, ProjectSpec } from "../types";

const EXAMPLES: { title: string; blurb: string; model_id: string; device_id: string; fps: number; ambient: number; latency: number | null; power?: number }[] = [
  { title: "Robot that spots objects", blurb: "YOLOv8s on a Jetson Orin NX in a warm room. It overheats today.", model_id: "yolov8s", device_id: "jetson-orin-nx", fps: 30, ambient: 40, latency: 100 },
  { title: "Smart camera", blurb: "MobileNetV2 on a Jetson Orin Nano. A comfortable fit.", model_id: "mobilenet-v2", device_id: "jetson-orin-nano", fps: 30, ambient: 30, latency: 100 },
  { title: "Fast, heavy vision", blurb: "ResNet-50 at 60 frames/s on a Jetson AGX Orin.", model_id: "resnet-50", device_id: "jetson-agx-orin", fps: 60, ambient: 30, latency: 50 },
  { title: "Raspberry Pi camera", blurb: "MobileNetV2 on a Pi 5 with no fan, in a warm cabinet.", model_id: "mobilenet-v2", device_id: "raspberry-pi-5", fps: 15, ambient: 40, latency: 100, power: 8 },
  { title: "Keyword spotting on an MCU", blurb: "A tiny speech model on an STM32H743: does it fit in flash and SRAM?", model_id: "tiny-kws-dscnn", device_id: "stm32h743", fps: 4, ambient: 30, latency: 250, power: 0.5 },
  { title: "Person detection on an FPGA", blurb: "A small vision model on a Kria KV260.", model_id: "tiny-vww-mobilenet", device_id: "xilinx-kria-kv260", fps: 60, ambient: 35, latency: 30, power: 10 },
];

function compact(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} G`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} M`;
  return `${(n / 1e3).toFixed(1)} k`;
}

const CATEGORY_OF: Record<string, "board_soc" | "mcu" | "fpga" | "accelerator" | "custom_system"> = {
  edge_soc: "board_soc", sbc: "board_soc", laptop_soc: "board_soc", datacenter_gpu: "board_soc",
  mcu: "mcu", fpga: "fpga", accelerator: "accelerator", custom: "custom_system",
};

export default function InputPage({
  spec,
  setSpec,
  models,
  devices,
  projectId,
  canWrite,
  onModelAdded,
  onDevicesChanged,
}: {
  spec: ProjectSpec;
  setSpec: (s: ProjectSpec) => void;
  models: ModelProfile[];
  devices: HardwareProfile[];
  projectId: number;
  canWrite: boolean;
  onModelAdded: (m: ModelProfile) => void;
  onDevicesChanged: () => void;
}) {
  const model = models.find((m) => m.id === spec.model_id);
  const device = devices.find((d) => d.id === spec.device_id);
  const [prefill, setPrefill] = useState<{ nonce: number; fields: Record<string, string>; source: string } | null>(null);
  const [draftKey, setDraftKey] = useState(0);
  const pickDevice = (d: HardwareProfile) => setSpec({ ...spec, device_id: d.id, cooling_id: d.default_cooling ?? null, baseline: null });

  function applyExample(p: (typeof EXAMPLES)[number]) {
    const d = devices.find((x) => x.id === p.device_id);
    setSpec({
      ...spec,
      model_id: p.model_id,
      device_id: p.device_id,
      cooling_id: d?.default_cooling ?? null,
      baseline: null,
      workload: { ...spec.workload, fps: p.fps, ambient_c: p.ambient },
      constraints: { ...spec.constraints, max_latency_ms: p.latency, ...(p.power ? { max_power_w: p.power } : {}) },
    });
  }

  return (
    <div className="input-page">
      <div className="ws-grid">
        <section className="panel" aria-labelledby="sw-h">
          <div className="card-head"><span className="tag-sw">Software</span><h2 id="sw-h">AI model</h2></div>
          <label className="field">
            Model
            <select value={spec.model_id} onChange={(e) => setSpec({ ...spec, model_id: e.target.value })} disabled={!canWrite}>
              <optgroup label="Library">
                {models.filter((m) => m.fidelity === "library").map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </optgroup>
              <optgroup label="Yours">
                {models.filter((m) => m.fidelity !== "library").map((m) => (
                  <option key={m.id} value={m.id}>{m.name} ({m.fidelity === "graph" ? "ONNX" : "entered by hand"})</option>
                ))}
              </optgroup>
            </select>
          </label>
          {model && (
            <dl className="facts-grid">
              <div><dt>Task</dt><dd>{model.task}</dd></div>
              <div><dt>Input</dt><dd>{model.input_desc}</dd></div>
              <div><dt>Parameters</dt><dd className="num">{compact(model.params_m * 1e6)}</dd></div>
              <div><dt>FLOPs per input</dt><dd className="num">{compact(model.gflops * 1e9)}</dd></div>
              <div><dt>Compute layers</dt><dd className="num">{model.n_layers}</dd></div>
              <div><dt>Source</dt><dd>{model.fidelity === "graph" ? "parsed from your ONNX graph" : model.fidelity === "manual" ? "entered by hand (wider bands)" : "published figures"}</dd></div>
            </dl>
          )}
          {canWrite && <ModelPanel onAdded={onModelAdded} />}
        </section>

        <section className="panel" aria-labelledby="hw-h">
          <div className="card-head"><span className="tag-hw">Hardware</span><h2 id="hw-h">Hardware system</h2></div>
          <DevicePicker devices={devices} value={spec.device_id} onPick={pickDevice} />
          {device && (
            <dl className="facts-grid">
              <div><dt>Class</dt><dd>{device.kind.replace("_", " ")}</dd></div>
              <div><dt>Peak compute</dt><dd className="num">{Math.max(...Object.values(device.peak_tflops)).toFixed(2)} T(FL)OPS</dd></div>
              <div><dt>Memory</dt><dd className="num">{device.mem_gb >= 1 ? `${device.mem_gb} GB` : `${(device.mem_gb * 1e6).toFixed(0)} KB`}, {device.mem_bw_gbs} GB/s</dd></div>
              <div><dt>Evidence</dt><dd>{device.verified ? "measured" : "datasheet / assumed"}</dd></div>
            </dl>
          )}
          {device?.notes && <p className="hint">{device.notes}</p>}
          {canWrite && (
            <>
              <CustomHardwareWizard prefill={prefill} onCreated={(d) => { onDevicesChanged(); pickDevice(d); }} />
              <HardwareImportCard onConfirmed={onDevicesChanged} refreshKey={draftKey} />
            </>
          )}
        </section>
      </div>

      {canWrite && (
        <section className="panel">
          <IntakePanel
            projectId={projectId}
            initialCategory={device ? CATEGORY_OF[device.kind] ?? "model" : "model"}
            handlers={{
              modelReady: onModelAdded,
              hardwareReady: (d) => { onDevicesChanged(); pickDevice(d); },
              applyProject: (sp, created) => { setSpec(sp); if (created) onDevicesChanged(); },
              prefill: (fields, source) => { setPrefill({ nonce: Date.now(), fields, source }); setDraftKey((k) => k + 1); },
            }}
          />
        </section>
      )}

      {canWrite && (
        <section className="panel" aria-labelledby="ex-h">
          <h2 id="ex-h">Or start from an example</h2>
          <p className="hint">Replaces the model, hardware and the main requirements of this project (saved as a new version when you save or analyse).</p>
          <div className="example-grid">
            {EXAMPLES.map((p) => (
              <button key={p.title} className="example" onClick={() => applyExample(p)}>
                <strong>{p.title}</strong>
                <span>{p.blurb}</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
