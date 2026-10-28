import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { HardwareProfile, HardwareStyle, ReportParse } from "../types";

type F = { key: string; label: string; unit?: string; hint?: string; required?: boolean };

const STYLES: Record<HardwareStyle, { title: string; blurb: string; fields: F[] }> = {
  generic: {
    title: "Board or SoC",
    blurb: "Anything with a datasheet: a custom carrier, a module, a SoC. Give peak throughput, memory and power.",
    fields: [
      { key: "peak_fp32", label: "FP32 peak", unit: "TFLOPS" },
      { key: "peak_fp16", label: "FP16 peak", unit: "TFLOPS" },
      { key: "peak_int8", label: "INT8 peak (dense)", unit: "TOPS", hint: "Halve vendor 'sparse' figures." },
      { key: "mem_bw_gbs", label: "Memory bandwidth", unit: "GB/s", required: true },
      { key: "mem_gb", label: "Memory size", unit: "GB", required: true },
      { key: "tdp_w", label: "Maximum power", unit: "W", required: true },
      { key: "idle_w", label: "Idle power", unit: "W", hint: "Blank = 12% of max (assumed)." },
    ],
  },
  mcu: {
    title: "Microcontroller",
    blurb: "STM32, nRF, ESP32, RP2040 and friends. Weights live in flash, activations in SRAM.",
    fields: [
      { key: "core_mhz", label: "Core clock", unit: "MHz", required: true },
      { key: "int8_macs_per_cycle", label: "int8 MACs per cycle", hint: "Cortex-M4/M7: 1-2 with SIMD; M55 Helium: 8-16; NPUs: hundreds.", required: true },
      { key: "flash_kb", label: "Flash for weights", unit: "KB", required: true },
      { key: "sram_kb", label: "SRAM for activations", unit: "KB", required: true },
      { key: "run_power_mw", label: "Power while running", unit: "mW", hint: "Datasheet run current x supply voltage, or a meter reading.", required: true },
      { key: "sleep_power_mw", label: "Power while sleeping", unit: "mW" },
    ],
  },
  fpga: {
    title: "FPGA",
    blurb: "Describe the fabric you will build the accelerator in. Paste Vivado reports below to fill most of it.",
    fields: [
      { key: "dsp_slices", label: "DSP slices available", required: true },
      { key: "fabric_mhz", label: "Accelerator clock", unit: "MHz", required: true },
      { key: "int8_macs_per_dsp", label: "int8 MACs per DSP", hint: "2 with DSP packing, 1 without." },
      { key: "achievable_dsp_use", label: "Usable fraction of DSPs", hint: "0.5-0.8 is typical after routing (default 0.7)." },
      { key: "bram_mb", label: "Block RAM", unit: "MB" },
      { key: "uram_mb", label: "UltraRAM", unit: "MB" },
      { key: "mem_bw_gbs", label: "External memory bandwidth", unit: "GB/s", required: true },
      { key: "mem_gb", label: "External memory size", unit: "GB", required: true },
      { key: "static_w", label: "Static power", unit: "W" },
      { key: "dynamic_w", label: "Dynamic power at that clock", unit: "W", required: true },
      { key: "theta_ja_k_per_w", label: "Thermal resistance (as built)", unit: "K/W" },
    ],
  },
  accelerator: {
    title: "AI accelerator",
    blurb: "A Hailo, Coral, NPU module. Say how much model fits on chip and how it connects to the host.",
    fields: [
      { key: "peak_int8", label: "INT8 peak (dense)", unit: "TOPS", required: true },
      { key: "peak_fp16", label: "FP16 peak", unit: "TFLOPS" },
      { key: "onchip_weights_mb", label: "On-chip model memory", unit: "MB", hint: "Weights beyond this stream over the link." },
      { key: "mem_bw_gbs", label: "Link / external bandwidth", unit: "GB/s", required: true },
      { key: "mem_gb", label: "Host memory it can use", unit: "GB", required: true },
      { key: "tdp_w", label: "Module power", unit: "W", required: true },
      { key: "idle_w", label: "Idle power", unit: "W" },
    ],
  },
};

const NUM = new Set(Object.values(STYLES).flatMap((s) => s.fields.map((f) => f.key)));

function body(style: HardwareStyle, name: string, vendor: string, v: Record<string, string>, datasheet: boolean, fpu: boolean, battery: string) {
  const b: Record<string, unknown> = { name, style, from_datasheet: datasheet };
  if (vendor) b.vendor = vendor;
  const peak: Record<string, number> = {};
  for (const [k, raw] of Object.entries(v)) {
    if (raw === "" || !NUM.has(k)) continue;
    const n = Number(raw);
    if (Number.isNaN(n)) continue;
    if (k.startsWith("peak_")) peak[k.slice(5)] = n;
    else b[k] = n;
  }
  if (Object.keys(peak).length) b.peak_tops = peak;
  if (style === "mcu") b.has_fpu = fpu;
  if (battery) b.battery_wh = Number(battery);
  return b;
}

export default function CustomHardwareWizard({
  onCreated,
  prefill,
}: {
  onCreated: (d: HardwareProfile) => void;
  prefill?: { nonce: number; fields: Record<string, string>; source: string } | null;
}) {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<HardwareStyle>("mcu");
  const [name, setName] = useState("");
  const [vendor, setVendor] = useState("");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [datasheet, setDatasheet] = useState(false);
  const [fpu, setFpu] = useState(false);
  const [battery, setBattery] = useState("");
  const [preview, setPreview] = useState<HardwareProfile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState("");
  const [parsed, setParsed] = useState<ReportParse | null>(null);

  const def = STYLES[style];
  const ready = name.trim().length >= 2 && def.fields.filter((f) => f.required).every((f) => vals[f.key] !== undefined && vals[f.key] !== "");
  const payload = useMemo(() => body(style, name.trim(), vendor, vals, datasheet, fpu, battery), [style, name, vendor, vals, datasheet, fpu, battery]);

  useEffect(() => {
    if (!ready) {
      setPreview(null);
      return;
    }
    const t = setTimeout(() => {
      api.customPreview(payload).then((p) => { setPreview(p); setErr(null); }).catch((e) => { setPreview(null); setErr(String(e)); });
    }, 350);
    return () => clearTimeout(t);
  }, [ready, payload]);

  useEffect(() => {
    if (!prefill) return;
    const f = { ...prefill.fields };
    const active = f.__power_active_w;
    const idle = f.__power_idle_w;
    delete f.__power_active_w;
    delete f.__power_idle_w;
    setVals((v) => {
      const next = { ...v, ...f };
      if (active) {
        next[style === "mcu" ? "run_power_mw" : "tdp_w"] = String(style === "mcu" ? +(Number(active) * 1000).toFixed(2) : +Number(active).toFixed(3));
        if (idle) next[style === "mcu" ? "sleep_power_mw" : "idle_w"] = String(style === "mcu" ? +(Number(idle) * 1000).toFixed(3) : +Number(idle).toFixed(3));
      }
      return next;
    });
    if (prefill.source.startsWith("vivado_util") || prefill.source === "vivado_power") setStyle("fpga");
    else if (prefill.source === "stedgeai" || prefill.source === "vela" || prefill.source === "datasheet_mcu") setStyle("mcu");
    else if (prefill.source === "datasheet") setStyle("generic");
    setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce]);

  async function readReport() {
    setErr(null);
    try {
      setParsed(await api.parseReport(report));
    } catch (e) {
      setParsed(null);
      setErr(String(e));
    }
  }
  function applyParsed() {
    if (!parsed) return;
    const next = { ...vals };
    for (const [k, v] of Object.entries(parsed.fields ?? {})) next[k] = String(v);
    if (parsed.tool === "power_csv") {
      if (parsed.power_w_active) next[style === "mcu" ? "run_power_mw" : "tdp_w"] = String(style === "mcu" ? +(parsed.power_w_active * 1000).toFixed(2) : +parsed.power_w_active.toFixed(3));
      if (parsed.power_w_idle !== undefined) next[style === "mcu" ? "sleep_power_mw" : "idle_w"] = String(style === "mcu" ? +(parsed.power_w_idle * 1000).toFixed(3) : +parsed.power_w_idle.toFixed(3));
    }
    setVals(next);
    setParsed(null);
    setReport("");
  }

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      onCreated(await api.customCreate(payload));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const tops = preview ? Math.max(...Object.values(preview.peak_tflops)) : 0;
  return (
    <details className="import-card wizard" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Describe your own hardware (board, MCU, FPGA, accelerator)</summary>
      <p className="hint">
        Enter what you know. Anything we have to guess (clock steps, cooling, how much of the peak real code reaches) is
        labelled <em>assumed</em> and can be replaced by a measurement later.
      </p>
      <div className="seg" role="tablist">
        {(Object.keys(STYLES) as HardwareStyle[]).map((s) => (
          <button key={s} className={s === style ? "on" : ""} onClick={() => { setStyle(s); setVals({}); setPreview(null); }}>{STYLES[s].title}</button>
        ))}
      </div>
      <p className="hint">{def.blurb}</p>
      <div className="grid2">
        <label className="field"><span>Name</span><input value={name} placeholder="My vision board rev B" onChange={(e) => setName(e.target.value)} /></label>
        <label className="field"><span>Vendor / team (optional)</span><input value={vendor} onChange={(e) => setVendor(e.target.value)} /></label>
        {def.fields.map((f) => (
          <label className="field" key={f.key} title={f.hint}>
            <span>{f.label}{f.unit ? ` (${f.unit})` : ""}{f.required ? " *" : ""}</span>
            <input type="number" step="any" value={vals[f.key] ?? ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
          </label>
        ))}
        <label className="field"><span>Battery capacity, if any (Wh)</span><input type="number" step="any" value={battery} onChange={(e) => setBattery(e.target.value)} /></label>
      </div>
      <div className="checks-inline">
        <label className="check-row"><input type="checkbox" checked={datasheet} onChange={(e) => setDatasheet(e.target.checked)} /><span>These numbers come from a datasheet or vendor tool (shown as vendor figures, not assumptions)</span></label>
        {style === "mcu" && <label className="check-row"><input type="checkbox" checked={fpu} onChange={(e) => setFpu(e.target.checked)} /><span>Has a single-precision FPU</span></label>}
      </div>

      <details>
        <summary className="muted">Fill from a vendor report or a power-meter capture</summary>
        <p className="hint">Paste a Vivado power or utilisation report, an ST Edge AI or Arm Vela summary, or a Joulescope / Otii / PPK2 CSV export.</p>
        <textarea rows={5} value={report} placeholder="Paste report text or CSV here" onChange={(e) => setReport(e.target.value)} />
        <div className="cta-row"><button disabled={report.trim().length < 10} onClick={readReport}>Read it</button></div>
        {parsed && (
          <div className="draft">
            <p className="hint"><strong>{parsed.tool}</strong>: {parsed.note}</p>
            <ul>
              {Object.entries(parsed.fields ?? {}).map(([k, v]) => <li key={k}>{k} = {v}</li>)}
              {parsed.power_w_avg !== undefined && <li>power: average {parsed.power_w_avg.toFixed(4)} W, active {parsed.power_w_active?.toFixed(4)} W, idle {parsed.power_w_idle?.toFixed(5)} W, peak {parsed.power_w_peak?.toFixed(4)} W ({parsed.samples} samples)</li>}
              {parsed.found && Object.entries(parsed.found).map(([k, v]) => <li key={k} className="muted">{k}: {JSON.stringify(v)}</li>)}
            </ul>
            <button onClick={applyParsed}>Use these values</button>
          </div>
        )}
      </details>

      {preview && (
        <div className="draft">
          <h3>What we will create</h3>
          <div className="facts">
            <div><span>Peak</span><strong>{tops >= 1 ? tops.toFixed(1) : (tops * 1000).toFixed(1) + " G"}OPS</strong></div>
            <div><span>Power</span><strong>{preview.idle_w < 0.1 ? (preview.idle_w * 1000).toFixed(1) + " mW" : preview.idle_w.toFixed(2) + " W"} idle to {Math.max(...preview.power_modes.map((m) => m.cap_w)).toFixed(2)} W</strong></div>
            <div><span>RAM</span><strong>{(preview.mem_gb ?? 0) < 1 ? ((preview.mem_gb ?? 0) * 1e3).toFixed(2) + " MB" : (preview.mem_gb ?? 0).toFixed(1) + " GB"}</strong></div>
            {preview.weights_mem_gb ? <div><span>Weight storage</span><strong>{(preview.weights_mem_gb * 1e3).toFixed(2)} MB</strong></div> : null}
          </div>
          <p className="hint">
            Assumed: {Object.entries(preview.sources ?? {}).filter(([, v]) => v === "assumed").map(([k]) => k.replace(/_/g, " ")).join(", ") || "nothing"}.
          </p>
          {preview.notes && <p className="note">{preview.notes}</p>}
        </div>
      )}
      {err && <p className="error">{err}</p>}
      <div className="cta-row">
        <button className="primary" disabled={!ready || !preview || busy} onClick={save}>{busy ? "Saving…" : "Save and use this hardware"}</button>
      </div>
    </details>
  );
}
