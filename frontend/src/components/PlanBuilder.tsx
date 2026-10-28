import { useEffect, useMemo, useState } from "react";
import { plans } from "../api";
import { Figure, num } from "../twin/chart";
import { usePlanPicker } from "../twin/TraceBar";
import type { DiffRows, PlanAnalysis, PlanChange, SavedPlan } from "../types";

const GROUPS: { tier: string; title: string; blurb: string }[] = [
  { tier: "model", title: "Change the AI model", blurb: "Precision, pruning, input size, a lighter model. Accuracy must be re-validated." },
  { tier: "software", title: "Change how it runs", blurb: "Batching and runtime settings." },
  { tier: "configure", title: "Configure the hardware you have", blurb: "Power mode and clocks." },
  { tier: "select", title: "Choose hardware or cooling", blurb: "Another board, or a cooling option from the catalogue." },
  { tier: "design", title: "Redesign the chip or board (directional)", blurb: "Analytical what-ifs on one parameter. Not validated designs." },
];

const METRIC_LABEL: Record<string, string> = {
  latency: "Latency per input", batch_time: "Time per batch", capacity_fps: "Capacity", power_avg: "Average power",
  power_peak: "Power while computing", energy_per_inference: "Energy per input", memory: "Memory",
  temperature: "Temperature", temperature_unthrottled: "Temperature without throttling", thermal_headroom: "Thermal headroom",
  clock_factor: "Clock after throttling", accuracy: "Accuracy", cost_per_day: "Electricity cost", carbon_per_day: "Emissions",
  battery_hours: "Battery life", weights: "Weights",
};

const PARAM_LABEL: Record<string, string> = {
  model: "AI model", parameters_m: "Model parameters", gflops: "Compute per input", device: "Hardware",
  peak_at_precision: "Peak compute at this precision", memory_bandwidth_gbs: "Memory bandwidth", memory_gb: "Memory size",
  idle_w: "Idle power", dynamic_w_max: "Maximum dynamic power", memory_w_max: "Maximum memory power",
  leakage_w_25c: "Leakage at 25 °C", precision: "Number format", batch: "Batch size", input_resolution: "Input resolution",
  pruned_fraction: "Share of the network pruned", power_mode: "Power mode", power_cap_w: "Power cap", clock: "Clock cap",
  cooling: "Cooling", cooling_r_th_k_per_w: "Cooling thermal resistance", cooling_active_w: "Fan / pump power",
};

function pctText(p: number | null) {
  if (p === null || !Number.isFinite(p)) return "n/a";
  if (Math.abs(p) < 0.5) return "no change";
  return `${p > 0 ? "+" : ""}${p.toFixed(1)}%`;
}

function Feas({ s }: { s: string }) {
  return <span className={`chip chip-${s}`}>{s.replace(/_/g, " ")}</span>;
}

export function DiffTables({ diff, showAll }: { diff: DiffRows; showAll: boolean }) {
  const params = diff.parameters.filter((p) => showAll || p.changed);
  return (
    <>
      <h3>Parameters</h3>
      <div className="table-wrap">
        <table className="tbl diff">
          <thead><tr><th>Parameter</th><th>Today</th><th>Plan</th><th>Unit</th></tr></thead>
          <tbody>
            {params.map((p) => (
              <tr key={p.name} className={p.changed ? "changed" : ""}>
                <td>{PARAM_LABEL[p.name] ?? p.name.replace(/_/g, " ")}</td>
                <td className="num">{typeof p.baseline === "number" ? num(p.baseline, 4) : p.baseline}</td>
                <td className="num"><strong>{typeof p.plan === "number" ? num(p.plan, 4) : p.plan}</strong></td>
                <td className="muted">{p.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Outputs</h3>
      <div className="table-wrap">
        <table className="tbl diff">
          <thead><tr><th>Metric</th><th className="num">Today (range)</th><th className="num">Plan (range)</th><th className="num">Change</th><th>Source</th></tr></thead>
          <tbody>
            {diff.metrics.map((m) => (
              <tr key={m.name}>
                <td>{METRIC_LABEL[m.name] ?? m.name} <span className="muted">{m.unit}</span></td>
                <td className="num">{num(m.baseline.value)} <span className="muted">({num(m.baseline.low)} to {num(m.baseline.high)})</span></td>
                <td className="num">{num(m.plan.value)} <span className="muted">({num(m.plan.low)} to {num(m.plan.high)})</span></td>
                <td className={`num ${m.better === true ? "good" : m.better === false ? "bad" : ""}`}>
                  {pctText(m.change_percent)}{m.better === true ? " (better)" : m.better === false ? " (worse)" : ""}
                </td>
                <td className="muted">{m.baseline.provenance === m.plan.provenance ? m.plan.provenance : `${m.baseline.provenance} → ${m.plan.provenance}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PlanResult({ a }: { a: PlanAnalysis }) {
  const [showAll, setShowAll] = useState(false);
  const energy = (d: DiffRows) => d.metrics.find((m) => m.name === "energy_per_inference");
  const base = a.baseline.metrics.energy_per_inference?.value ?? 0;
  const bars = [
    { label: "Today", value: base, feas: a.baseline.feasibility },
    ...a.singles.map((s) => ({ label: s.change.title, value: s.view.metrics.energy_per_inference?.value ?? 0, feas: s.view.feasibility })),
    { label: "Combined plan", value: a.plan.metrics.energy_per_inference?.value ?? 0, feas: a.plan.feasibility },
  ];
  const max = Math.max(...bars.map((b) => b.value), 1e-12);
  const pick = (k: string) => a.diff.metrics.find((m) => m.name === k);
  return (
    <div className="plan-result">
      <div className="kpis">
        <div><span>Today</span><strong><Feas s={a.baseline.feasibility} /></strong><small>{a.baseline.bottleneck}-limited{a.baseline.throttled ? ", throttling" : ""}</small></div>
        <div><span>With the plan</span><strong><Feas s={a.plan.feasibility} /></strong><small>{a.plan.bottleneck}-limited{a.plan.throttled ? ", throttling" : ""}</small></div>
        {(["energy_per_inference", "power_avg", "latency", "temperature"] as const).map((k) => {
          const m = pick(k);
          return m ? (
            <div key={k}><span>{METRIC_LABEL[k]}</span><strong className={`num ${m.better ? "good" : m.better === false ? "bad" : ""}`}>{pctText(m.change_percent)}</strong>
              <small>{num(m.baseline.value)} → {num(m.plan.value)} {m.unit}</small></div>
          ) : null;
        })}
      </div>
      <p className="note">{a.accuracy}</p>
      {a.applied.notes.map((n) => <p key={n} className="note-info">{n}</p>)}

      <Figure
        title="Energy per input: today, each change alone, combined"
        note="Each change applied on its own to today's setup, then all together. Changes interact, so the combined result is not the sum of the parts."
        table={<table className="tbl"><tbody>{bars.map((b) => <tr key={b.label}><td>{b.label}</td><td className="num">{num(b.value)} mJ</td><td>{b.feas.replace(/_/g, " ")}</td></tr>)}</tbody></table>}
      >
        <ul className="hbars">
          {bars.map((b, i) => (
            <li key={b.label}>
              <span className="hb-label" title={b.label}>{b.label}</span>
              <span className="hb-track"><span className="hb-bar" style={{ width: `${(100 * b.value) / max}%`, background: i === bars.length - 1 ? "var(--s3)" : i === 0 ? "var(--line-strong)" : "var(--s1)" }} /></span>
              <span className="hb-val num">{num(b.value)} mJ</span>
            </li>
          ))}
        </ul>
      </Figure>

      <section className="panel">
        <div className="row">
          <h2>Today vs the plan</h2>
          <label className="check-row"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show unchanged parameters</label>
        </div>
        <DiffTables diff={a.diff} showAll={showAll} />
      </section>

      <section className="panel">
        <h2>Each change on its own</h2>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Change</th><th>Result</th><th className="num">Energy</th><th className="num">Power</th><th className="num">Latency</th><th>Confidence</th></tr></thead>
            <tbody>
              {a.singles.map((s) => {
                const e = energy(s.diff);
                const p = s.diff.metrics.find((m) => m.name === "power_avg");
                const l = s.diff.metrics.find((m) => m.name === "latency");
                return (
                  <tr key={s.change.id}>
                    <td>{s.change.title}{s.change.directional && <span className="badge"> directional</span>}</td>
                    <td><Feas s={s.view.feasibility} /></td>
                    <td className="num">{pctText(e?.change_percent ?? null)}</td>
                    <td className="num">{pctText(p?.change_percent ?? null)}</td>
                    <td className="num">{pctText(l?.change_percent ?? null)}</td>
                    <td>{s.change.confidence}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {a.risks.length > 0 && (
          <>
            <h3>Risks and what to check</h3>
            <ul>{a.risks.map((r) => <li key={r.change}><strong>{r.change}:</strong> {r.risk}</li>)}</ul>
          </>
        )}
        {a.directional.length > 0 && <p className="note">Directional items ({a.directional.join(", ")}) are analytical what-ifs. No PCB, RTL or chip-design file is produced for them.</p>}
      </section>
    </div>
  );
}

export default function PlanBuilder({
  projectId,
  canWrite,
  initialAdd,
  onSaved,
}: {
  projectId: number;
  canWrite: boolean;
  initialAdd?: string | null;
  onSaved: (p: SavedPlan) => void;
}) {
  const [opts, setOpts] = useState<{ recommended: string[]; changes: PlanChange[] } | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [analysis, setAnalysis] = useState<PlanAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("Design plan");
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<SavedPlan | null>(null);
  const pickPlan = usePlanPicker();

  useEffect(() => {
    plans
      .options(projectId)
      .then((o) => {
        setOpts(o);
        setSel(Array.from(new Set([...o.recommended, ...(initialAdd ? initialAdd.split(",") : [])])));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [projectId, initialAdd]);

  const byTier = useMemo(() => {
    const m = new Map<string, PlanChange[]>();
    for (const c of opts?.changes ?? []) m.set(c.tier, [...(m.get(c.tier) ?? []), c]);
    return m;
  }, [opts]);

  const toggle = (id: string) => {
    setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
    setAnalysis(null);
    setSaved(null);
  };

  async function analyse() {
    setBusy(true);
    setError(null);
    try {
      const a = await plans.analyse(projectId, sel);
      setAnalysis(a);
      // Carry the analysed plan to the map stages, which can then show its trace.
      pickPlan?.({ key: sel.join("+"), label: a.changes.map((c) => c.title).join("; "), changeIds: [...sel] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const p = await plans.save(projectId, sel, name, note);
      setSaved(p);
      onSaved(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !opts) return <p className="error" role="alert">{error}</p>;
  if (!opts) return <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w60" /></div>;
  const recommended = new Set(opts.recommended);

  return (
    <div className="plan-builder">
      <section className="panel">
        <h2>Choose the changes</h2>
        <p className="hint">The recommended setup is preselected. Add or remove anything; the analysis re-runs the engine for today's setup, each change alone, and the combination.</p>
        {GROUPS.map((g) => {
          const items = byTier.get(g.tier) ?? [];
          if (!items.length) return null;
          return (
            <fieldset key={g.tier} className={`plan-group tier-${g.tier}`}>
              <legend>{g.title}</legend>
              <p className="hint">{g.blurb}</p>
              {items.map((c) => (
                <label key={c.id} className={`plan-opt ${sel.includes(c.id) ? "on" : ""}`}>
                  <input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!canWrite} />
                  <span>
                    <strong>{c.title}</strong>
                    {recommended.has(c.id) && <span className="badge"> recommended</span>}
                    {c.directional && <span className="badge badge-assumed"> directional</span>}
                    <small className="block muted">{c.risk} Confidence: {c.confidence}.</small>
                  </span>
                </label>
              ))}
            </fieldset>
          );
        })}
        <div className="actions">
          <button className="primary" disabled={busy || !sel.length || !canWrite} onClick={analyse}>
            {busy && !analysis ? "Analysing…" : `Analyse this plan (${sel.length} change${sel.length === 1 ? "" : "s"})`}
          </button>
          {sel.length > 0 && <button className="link" onClick={() => { setSel([]); setAnalysis(null); }}>Clear</button>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      {analysis && (
        <>
          <PlanResult a={analysis} />
          <section className="panel">
            <h2>Save this plan</h2>
            {saved ? (
              <p className="note-ok" role="status">Saved “{saved.name}” as version {saved.version}. Find it under Saved plans, or export it under Verify, history, export.</p>
            ) : (
              <form className="inline-form" onSubmit={save}>
                <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required /></label>
                <label className="field">Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} /></label>
                <button className="primary" disabled={busy || !canWrite}>Save plan</button>
              </form>
            )}
            <p className="hint">Saving again under the same name adds a new version; saved versions never change.</p>
          </section>
        </>
      )}
    </div>
  );
}
