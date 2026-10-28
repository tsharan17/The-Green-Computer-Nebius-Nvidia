import { GLOSSARY } from "../lib/plain";
import type { Config, Constraints, Environment, HardwareProfile, ProjectSpec, Workload } from "../types";

const NO_LIMITS: Environment = { sealed_enclosure: false, dusty: false, max_noise_dba: null, max_height_mm: null, datacenter: false, facility_water: false };

function Num({
  label,
  unit,
  value,
  onChange,
  hint,
  min,
  step,
  placeholder,
  disabled,
}: {
  label: string;
  unit: string;
  value: number | "" | null;
  onChange: (v: string) => void;
  hint?: string;
  min?: number;
  step?: number;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <label className="req-row" title={hint}>
      <span className="req-label">{label}</span>
      <span className="req-input">
        <input type="number" value={value ?? ""} min={min} step={step} placeholder={placeholder} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        <em>{unit}</em>
      </span>
      {hint && <small className="hint block">{hint}</small>}
    </label>
  );
}

export default function ConstraintsPage({
  spec,
  setSpec,
  device,
  canWrite,
}: {
  spec: ProjectSpec;
  setSpec: (s: ProjectSpec) => void;
  device: HardwareProfile | undefined;
  canWrite: boolean;
}) {
  const setW = (patch: Partial<Workload>) => setSpec({ ...spec, workload: { ...spec.workload, ...patch } });
  const setC = (patch: Partial<Constraints>) => setSpec({ ...spec, constraints: { ...spec.constraints, ...patch } });
  const env: Environment = { ...NO_LIMITS, ...spec.environment };
  const setE = (patch: Partial<Environment>) => setSpec({ ...spec, environment: { ...env, ...patch } });
  const num = (v: string, fallback: number) => (v === "" || Number.isNaN(Number(v)) ? fallback : Number(v));
  const c = spec.constraints;
  const w = spec.workload;
  const ro = !canWrite;

  const top = device ? [...device.power_modes].sort((a, b) => b.cap_w - a.cap_w)[0] : undefined;
  const base: Config | null = spec.baseline;
  const setBase = (patch: Partial<Config> | null) => {
    if (!device || !top) return;
    if (patch === null) return setSpec({ ...spec, baseline: null });
    const cur: Config = base ?? {
      precision: (["fp32", "fp16", "int8"] as const).find((p) => (device.peak_tflops[p] ?? 0) > 0) ?? "int8",
      batch: 1,
      power_mode_id: top.id,
      cooling_id: spec.cooling_id ?? device.default_cooling,
      resolution_scale: 1,
      clock_trim: 1,
      sparsity: 0,
    };
    setSpec({ ...spec, baseline: { ...cur, ...patch, cooling_id: spec.cooling_id ?? device.default_cooling } });
  };

  return (
    <div className="constraints-page">
      <section className="panel" aria-labelledby="perf-h">
        <div className="card-head"><span className="tag-rq">Requirements</span><h2 id="perf-h">Performance constraints</h2></div>
        <p className="hint">Hard limits. A design passes only if its pessimistic (worst-case) prediction meets every one; we never relax them to make a result look better.</p>
        <div className="reqs two-col">
          <Num label="Throughput, at least" unit="inputs per second" value={w.fps} onChange={(v) => setW({ fps: Math.max(0.001, num(v, 1)) })} hint={GLOSSARY.fps} disabled={ro} />
          <Num label="Average power, at most" unit="W" value={c.max_power_w} step={0.1} onChange={(v) => setC({ max_power_w: num(v, 55) })} disabled={ro} />
          <Num label="Latency per input, at most" unit="ms (blank: no limit)" value={c.max_latency_ms} placeholder="no limit" onChange={(v) => setC({ max_latency_ms: v === "" ? null : Number(v) })} hint={GLOSSARY.latency} disabled={ro} />
          <Num label="Memory, at most" unit="GB (blank: device limit)" value={c.max_memory_gb} step={0.01} placeholder="device limit" onChange={(v) => setC({ max_memory_gb: v === "" ? null : Number(v) })} disabled={ro} />
          <Num label="Accuracy, at least" unit={c.accuracy_mode === "relative" ? "% of the model's own accuracy" : "% (absolute metric, e.g. mAP)"} value={Math.round(c.min_accuracy * 1000) / 10} step={0.1} onChange={(v) => setC({ min_accuracy: Math.min(1, Math.max(0.001, num(v, 90) / 100)) })} disabled={ro} />
          <label className="req-row">
            <span className="req-label">Accuracy is measured as</span>
            <select value={c.accuracy_mode} disabled={ro} onChange={(e) => setC({ accuracy_mode: e.target.value as Constraints["accuracy_mode"] })}>
              <option value="relative">a share of the model's own accuracy</option>
              <option value="absolute">the model's absolute score</option>
            </select>
          </label>
          <label className="check-row">
            <input type="checkbox" checked={c.forbid_throttling} disabled={ro} onChange={(e) => setC({ forbid_throttling: e.target.checked })} />
            <span>Must not thermally throttle under sustained load <small className="muted">({GLOSSARY.throttle})</small></span>
          </label>
        </div>
      </section>

      <section className="panel" aria-labelledby="env-h">
        <div className="card-head"><span className="tag-rq">Environment</span><h2 id="env-h">Environment and operation</h2></div>
        <div className="reqs two-col">
          <Num label="Ambient temperature" unit="°C" value={w.ambient_c} onChange={(v) => setW({ ambient_c: num(v, 25) })} disabled={ro} />
          <label className="req-row">
            <span className="req-label">Cooling fitted today</span>
            <select value={spec.cooling_id ?? ""} disabled={ro} onChange={(e) => setSpec({ ...spec, cooling_id: e.target.value, baseline: base ? { ...base, cooling_id: e.target.value } : null })}>
              {device?.cooling_options.map((o) => <option key={o.id} value={o.id}>{o.name} ({o.r_th_k_per_w} K/W{o.fan_w ? `, fan ${o.fan_w} W` : ""})</option>)}
            </select>
          </label>
          <Num label="Hours of operation per day" unit="h" value={w.hours_per_day} step={0.5} onChange={(v) => setW({ hours_per_day: Math.min(24, Math.max(0, num(v, 24))) })} disabled={ro} />
          <Num label="Electricity price" unit="per kWh" value={w.price_per_kwh} step={0.01} onChange={(v) => setW({ price_per_kwh: num(v, 0.15) })} disabled={ro} />
          <Num label="Grid carbon intensity" unit="gCO₂e per kWh" value={w.grid_gco2_per_kwh} onChange={(v) => setW({ grid_gco2_per_kwh: num(v, 400) })} hint="Your grid's average; national figures are published yearly." disabled={ro} />
          <label className="check-row">
            <input type="checkbox" checked={w.streaming} disabled={ro} onChange={(e) => setW({ streaming: e.target.checked })} />
            <span>Inputs arrive live, one at a time (a batch waits for its inputs) <small className="muted">({GLOSSARY.batch})</small></span>
          </label>
        </div>
      </section>

      <section className="panel" aria-labelledby="where-h">
        <div className="card-head"><span className="tag-rq">Environment</span><h2 id="where-h">Where it runs</h2></div>
        <p className="hint">Rules out cooling that cannot be used there. Leave everything unset if any cooling is possible.</p>
        <div className="reqs two-col">
          {([
            ["sealed_enclosure", "Sealed enclosure (no vents or open fins)"],
            ["dusty", "Dusty site (fans and vents need filters)"],
            ["datacenter", "In a data-centre rack"],
            ["facility_water", "A facility water loop is available"],
          ] as const).map(([k, label]) => (
            <label key={k} className="check-row">
              <input type="checkbox" checked={!!env[k]} disabled={ro} onChange={(e) => setE({ [k]: e.target.checked })} />
              <span>{label}</span>
            </label>
          ))}
          <Num label="Noise, at most" unit="dBA at 1 m (blank: no limit)" value={env.max_noise_dba} placeholder="no limit" onChange={(v) => setE({ max_noise_dba: v === "" ? null : Number(v) })} disabled={ro} />
          <Num label="Room above the board" unit="mm (blank: no limit)" value={env.max_height_mm} placeholder="no limit" onChange={(v) => setE({ max_height_mm: v === "" ? null : Number(v) })} disabled={ro} />
        </div>
      </section>

      <section className="panel" aria-labelledby="cur-h">
        <div className="card-head"><span className="tag-sw">Baseline</span><h2 id="cur-h">How it runs today</h2></div>
        <p className="hint">The configuration every recommendation is compared against. Leave it on the default (full precision, batch 1, highest power mode) if you have not deployed yet.</p>
        {device && top ? (
          <div className="reqs two-col">
            <label className="req-row">
              <span className="req-label">Number format</span>
              <select value={base?.precision ?? ""} disabled={ro} onChange={(e) => (e.target.value ? setBase({ precision: e.target.value as Config["precision"] }) : setBase(null))}>
                <option value="">Default</option>
                {(["fp32", "fp16", "int8"] as const).filter((p) => (device.peak_tflops[p] ?? 0) > 0).map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
              </select>
            </label>
            <Num label="Batch size" unit="inputs" value={base?.batch ?? 1} min={1} onChange={(v) => setBase({ batch: Math.max(1, Math.round(num(v, 1))) })} disabled={ro} />
            <label className="req-row">
              <span className="req-label">Power mode</span>
              <select value={base?.power_mode_id ?? top.id} disabled={ro} onChange={(e) => setBase({ power_mode_id: e.target.value })}>
                {device.power_modes.map((m) => <option key={m.id} value={m.id}>{m.name} (cap {m.cap_w} W)</option>)}
              </select>
            </label>
            {base && <button className="link" onClick={() => setBase(null)} disabled={ro}>Reset to the default baseline</button>}
          </div>
        ) : (
          <p className="empty">Choose hardware first.</p>
        )}
      </section>
    </div>
  );
}
