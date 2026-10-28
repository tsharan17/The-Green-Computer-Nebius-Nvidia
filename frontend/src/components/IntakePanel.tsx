import { useEffect, useRef, useState } from "react";
import { api, download, intake } from "../api";
import type { FileFormat, FormatCatalogue, HardwareProfile, IntakeResult, ModelProfile, ProjectSpec } from "../types";

export interface IntakeHandlers {
  modelReady: (m: ModelProfile) => void;
  hardwareReady: (d: HardwareProfile) => void;
  applyProject: (spec: ProjectSpec, hardwareCreated: boolean) => void;
  prefill: (fields: Record<string, string>, source: string) => void;
}

type Cat = "model" | "board_soc" | "mcu" | "fpga" | "accelerator" | "custom_system";
const CAT_LABEL: Record<Cat, string> = {
  model: "AI model",
  board_soc: "Board / SoC",
  mcu: "Microcontroller",
  fpga: "FPGA",
  accelerator: "AI accelerator",
  custom_system: "Custom system",
};

interface Entry {
  id: number;
  file: File;
  state: "working" | "done" | "error";
  result?: IntakeResult;
  error?: string;
  action?: string;
}

let seq = 1;

function kb(n: number) {
  return `${(n / 1024).toFixed(1)} KB`;
}

/** Datasheet candidates -> fields of the "Describe your hardware" form. */
function toFields(cands: NonNullable<IntakeResult["report"]>["candidates"], mcu: boolean): Record<string, string> {
  const f: Record<string, string> = {};
  for (const c of cands ?? []) {
    const u = c.unit.toUpperCase();
    if (c.field === "peak_compute") {
      const v = /^G/.test(u) ? c.value / 1000 : /^P/.test(u) ? c.value * 1000 : c.value;
      const key = c.qualifier === "fp32" ? "peak_fp32" : c.qualifier === "fp16" || c.qualifier === "bf16" ? "peak_fp16" : "peak_int8";
      f[key] ??= String(v);
    } else if (c.field === "mem_bw_gbs") f.mem_bw_gbs ??= String(/TB/.test(u) ? c.value * 1000 : c.value);
    else if (c.field === "memory") {
      const scale = /^G/.test(u) ? 1 : /^M/.test(u) ? 1e-3 : 1e-6;
      if (/FLASH/.test(u) && mcu) f.flash_kb ??= String(c.value * scale * 1e6);
      else if (/SRAM/.test(u) && mcu) f.sram_kb ??= String(c.value * scale * 1e6);
      else if (!mcu) f.mem_gb ??= String(c.value * scale);
    } else if (c.field === "power_w" && /max|tdp/.test(c.qualifier ?? "")) {
      const w = u === "MW" ? c.value / 1000 : c.value;
      if (mcu) f.run_power_mw ??= String(w * 1000);
      else f.tdp_w ??= String(w);
    }
  }
  return f;
}

export default function IntakePanel({
  projectId,
  handlers,
  disabled,
  initialCategory = "model",
}: {
  projectId: number;
  handlers: IntakeHandlers;
  disabled?: boolean;
  initialCategory?: Cat;
}) {
  const [cat, setCat] = useState<Cat>(initialCategory);
  const [formats, setFormats] = useState<FormatCatalogue | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [over, setOver] = useState(false);
  const [dynDim, setDynDim] = useState<Record<number, string>>({});
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    intake.formats().then(setFormats).catch(() => setFormats(null));
  }, []);

  const list: FileFormat[] = cat === "model" ? formats?.model ?? [] : formats?.categories.find((c) => c.id === cat)?.formats ?? [];
  const accept = Array.from(new Set(list.flatMap((f) => f.extensions))).join(",");
  const patch = (id: number, p: Partial<Entry>) => setEntries((xs) => xs.map((x) => (x.id === id ? { ...x, ...p } : x)));

  async function send(file: File, id: number, dyn?: number) {
    patch(id, { state: "working", error: undefined });
    try {
      const r = await intake.upload(file, { category: cat, projectId, dynamicDim: dyn });
      patch(id, { state: "done", result: r });
      if (r.kind === "onnx_model" && r.status === "valid" && r.model) {
        handlers.modelReady(r.model);
        patch(id, { action: "Selected as the project's model." });
      }
      if (r.kind === "project_file" && r.status === "valid" && r.project) {
        handlers.applyProject(r.project.spec, !!r.project.hardware_created);
        patch(id, { action: "Loaded into this project's inputs (save or analyse to keep it)." });
      }
    } catch (e) {
      patch(id, { state: "error", error: e instanceof Error ? e.message : String(e) });
    }
  }

  function take(files: FileList | File[] | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const id = seq++;
      setEntries((xs) => [{ id, file, state: "working" }, ...xs]);
      void send(file, id);
    }
  }

  async function act(e: Entry, fn: () => Promise<string>) {
    try {
      patch(e.id, { action: await fn() });
    } catch (err) {
      patch(e.id, { action: `Could not: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  return (
    <section className="intake" aria-labelledby="intake-h">
      <div className="intake-head">
        <h2 id="intake-h">Upload files</h2>
        <p className="muted">Pick what you are describing. Each kind of hardware accepts different files; formats without a parser are listed as planned with what to send instead.</p>
      </div>
      <div className="seg cat-seg" role="tablist" aria-label="What are you uploading?">
        {(Object.keys(CAT_LABEL) as Cat[]).map((c) => (
          <button key={c} role="tab" aria-selected={cat === c} className={cat === c ? "on" : ""} onClick={() => setCat(c)}>
            {CAT_LABEL[c]}
          </button>
        ))}
      </div>

      <div className="table-wrap">
        <table className="tbl formats">
          <thead><tr><th>Format</th><th>Files</th><th>Status</th><th>What it gives the analysis / what to send</th></tr></thead>
          <tbody>
            {list.map((f) => (
              <tr key={f.id} className={`fmt-${f.status}`}>
                <td><strong>{f.name}</strong></td>
                <td className="muted">{f.extensions.join(" ")}</td>
                <td><span className={`chip chip-${f.status === "supported" ? "predicted_feasible" : f.status === "planned" ? "uncertain" : "infeasible"}`}>{f.status}</span></td>
                <td>
                  <span>{f.gives}</span>
                  <small className="muted block">{f.required}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cat !== "model" && (
        <p className="hint">
          Starting from scratch?{" "}
          <button className="link" onClick={() => download("/intake/templates/device-profile.yaml", "device-profile-template.yaml")}>
            Download the device profile template
          </button>{" "}
          (YAML, every field explained by example; values may carry units such as “68 GB/s”).
        </p>
      )}

      <div
        className={`dropzone ${over ? "over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!disabled) take(e.dataTransfer.files);
        }}
      >
        <div className="drop-main">
          <div>
            <h3 className="drop-title">Drop {CAT_LABEL[cat].toLowerCase()} files here</h3>
            <p className="muted">Up to {cat === "model" ? "100" : "20"} MB each. Files are checked, stored with this project and listed in its history.</p>
          </div>
          <button className="primary" disabled={disabled} onClick={() => input.current?.click()}>Choose files</button>
          <input ref={input} type="file" multiple hidden accept={accept || undefined} onChange={(e) => { take(e.target.files); e.target.value = ""; }} />
        </div>
      </div>

      {entries.length > 0 && (
        <ul className="intake-results" aria-live="polite">
          {entries.map((e) => {
            const r = e.result;
            const st = e.state === "error" ? "invalid" : r?.status ?? "working";
            return (
              <li key={e.id} className={`intake-item st-${st}`}>
                <div className="row">
                  <strong>{e.file.name}</strong>
                  <span className={`chip chip-${st === "valid" ? "predicted_feasible" : st === "invalid" ? "infeasible" : st === "working" ? "" : "uncertain"}`}>
                    {e.state === "working" ? "checking…" : st.replace("_", " ")}
                  </span>
                </div>
                {e.error && <p className="error">{e.error}</p>}
                {r && <p>{r.message}</p>}
                {r?.required && <p className="note-info">What works today: {r.required}</p>}

                {r?.kind === "onnx_model" && r.needs_dynamic_dim && (
                  <form className="inline-form" onSubmit={(ev) => { ev.preventDefault(); void send(e.file, e.id, Number(dynDim[e.id]) || undefined); }}>
                    <label className="field">Value for dynamic dimensions (e.g. input height/width)
                      <input type="number" min={1} value={dynDim[e.id] ?? "224"} onChange={(ev) => setDynDim((d) => ({ ...d, [e.id]: ev.target.value }))} />
                    </label>
                    <button>Check again</button>
                  </form>
                )}
                {r?.kind === "onnx_model" && r.status === "valid" && r.report && (
                  <details open>
                    <summary>Graph summary</summary>
                    <dl className="kv">
                      <dt>Inputs</dt><dd>{r.report.inputs?.map((i) => `${i.name} ${i.dtype} [${i.shape.join("×")}]`).join(", ")}</dd>
                      <dt>Outputs</dt><dd>{r.report.outputs?.map((i) => `${i.name} [${i.shape.join("×")}]`).join(", ")}</dd>
                      <dt>Opset</dt><dd>{Object.entries(r.report.opsets ?? {}).map(([k, v]) => `${k} ${v}`).join(", ")}</dd>
                      <dt>Nodes</dt><dd className="num">{r.report.n_nodes} ({Object.entries(r.report.op_histogram ?? {}).slice(0, 8).map(([k, v]) => `${k} ×${v}`).join(", ")})</dd>
                      <dt>Producer</dt><dd>{r.report.producer || "–"}</dd>
                    </dl>
                    {(r.warnings ?? []).map((w) => <p key={w} className="note">{w}</p>)}
                  </details>
                )}
                {r?.kind === "device_profile" && r.report && (
                  <>
                    {(r.report.errors ?? []).length > 0 && <ul className="problems">{r.report.errors!.map((x) => <li key={x}>{x}</li>)}</ul>}
                    {(r.report.notes ?? []).map((x) => <p key={x} className="hint">{x}</p>)}
                    {(r.report.warnings ?? []).map((x) => <p key={x} className="note">{x}</p>)}
                    {r.status === "valid" && r.report.profile && (
                      <button className="primary" onClick={() => act(e, async () => {
                        const d = await api.registerProfile(r.report!.profile as unknown as HardwareProfile);
                        handlers.hardwareReady(d);
                        return `Added “${d.name}” to your hardware and selected it.`;
                      })}>Add to my hardware</button>
                    )}
                  </>
                )}
                {r?.kind === "hardware_description" && r.status === "valid" && r.spec && (
                  <button className="primary" onClick={() => act(e, async () => {
                    const d = await api.customCreate(r.spec!);
                    handlers.hardwareReady(d);
                    return `Created “${d.name}” and selected it. Values you did not give are marked as assumptions.`;
                  })}>Create this hardware</button>
                )}
                {r?.kind === "firmware_elf" && r.report && (
                  <>
                    <p className="muted">{r.report.note}</p>
                    {(r.report.checks ?? []).map((c) => (
                      <p key={c.name} className={c.status === "fail" ? "error" : c.status === "warn" ? "note" : "note-ok"}>
                        {c.name}: {c.used_kb.toFixed(1)} KB of {c.limit_kb.toFixed(0)} KB on {r.report!.device} ({(c.share * 100).toFixed(0)}%)
                      </p>
                    ))}
                    <details><summary>Sections</summary>
                      <table className="tbl"><tbody>{(r.report.sections ?? []).map((s) => (
                        <tr key={s.name}><td>{s.name}</td><td>{s.region}</td><td className="num">{kb(s.size)}</td></tr>
                      ))}</tbody></table>
                    </details>
                  </>
                )}
                {r?.kind === "datasheet_pdf" && r.report && (
                  <>
                    {r.report.scanned_pages && r.report.scanned_pages.length > 0 && (
                      <p className="note">Pages without text (scans): {r.report.scanned_pages.join(", ")}. {r.report.ocr?.detail}</p>
                    )}
                    {(r.report.candidates ?? []).length > 0 && (
                      <>
                        <table className="tbl">
                          <thead><tr><th>Found</th><th>Value</th><th>Page</th><th>From the line</th></tr></thead>
                          <tbody>{r.report.candidates!.map((c, i) => (
                            <tr key={i}><td>{c.field.replace(/_/g, " ")}{c.qualifier ? ` (${c.qualifier})` : ""}</td><td className="num">{c.value} {c.unit}</td><td className="num">{c.page}</td><td className="muted">{c.line}</td></tr>
                          ))}</tbody>
                        </table>
                        <button onClick={() => act(e, async () => {
                          const f = toFields(r.report!.candidates, cat === "mcu");
                          handlers.prefill(f, cat === "mcu" ? "datasheet_mcu" : "datasheet");
                          return `Copied ${Object.keys(f).length} value(s) into “Describe your hardware” below. Check each before creating the device.`;
                        })}>Copy into the hardware form</button>
                      </>
                    )}
                  </>
                )}
                {r?.kind === "power_log" && r.report && (
                  <button onClick={() => act(e, async () => {
                    handlers.prefill({ __power_active_w: String(r.report!.power_w_active), __power_idle_w: String(r.report!.power_w_idle) }, "power_csv");
                    return "Offered the measured active and idle power to the hardware form.";
                  })}>Use in the hardware form</button>
                )}
                {r?.kind === "vendor_report" && r.status === "valid" && r.report?.fields && (
                  <button onClick={() => act(e, async () => {
                    handlers.prefill(Object.fromEntries(Object.entries(r.report!.fields!).map(([k, v]) => [k, String(v)])), String(r.report!.tool));
                    return "Copied the report's numbers into the hardware form.";
                  })}>Use in the hardware form</button>
                )}
                {r?.kind === "measurement_file" && <p className="hint">Import these under Verify, history, export to compare with predictions.</p>}
                {e.action && <p className="note-ok" role="status">{e.action}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
