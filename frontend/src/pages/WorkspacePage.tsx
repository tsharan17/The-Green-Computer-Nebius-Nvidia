import { useCallback, useEffect, useState } from "react";
import { api, guide, type Health, history, workspace } from "../api";
import { ErrorNote, Outcome } from "../components/Help";
import { useMode } from "../lib/mode";
import { navigate } from "../router";
import type { HardwareProfile, Me, ModelProfile, ProjectOut, ProjectSpec, UsageSummary } from "../types";

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function when(iso: string | null | undefined) {
  if (!iso) return "–";
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function bottleneckShort(b: string | null | undefined) {
  if (!b) return "–";
  const i = b.indexOf(":");
  const s = i > 0 ? b.slice(0, i) : b;
  return s[0].toUpperCase() + s.slice(1);
}

export default function WorkspacePage({ me, models, devices }: { me: Me; models: ModelProfile[]; devices: HardwareProfile[] }) {
  const { onboarded } = useMode();
  const [projects, setProjects] = useState<ProjectOut[] | null>(null);
  const [healthBy, setHealthBy] = useState<Record<number, Health>>({});
  const [showArchived, setShowArchived] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoMsg, setDemoMsg] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [from, setFrom] = useState<string>("example");
  const [busy, setBusy] = useState(false);
  const ws = me.workspaces.find((w) => w.id === me.active_workspace_id);
  const canWrite = me.role === "owner" || me.role === "editor";

  const load = useCallback(async () => {
    try {
      const [p, h, u] = await Promise.all([history.projects(showArchived), guide.health(), workspace.usage()]);
      setProjects(p);
      setHealthBy(Object.fromEntries(h.map((x) => [x.project_id, x])));
      setUsage(u);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [showArchived]);
  useEffect(() => {
    load();
  }, [load]);

  const label = (s: ProjectSpec) =>
    `${models.find((m) => m.id === s.model_id)?.name ?? s.model_id} on ${devices.find((d) => d.id === s.device_id)?.name ?? s.device_id}`;
  const analysedAny = Object.values(healthBy).some((h) => h.analysed);
  const firstVisit = !onboarded && !analysedAny;

  async function addDemo() {
    setDemoBusy(true);
    setDemoMsg(null);
    try {
      const r = await guide.demo();
      setDemoMsg(r.created.length ? `Added ${r.created.length} analysed example projects.` : "The example projects are already here.");
      load();
    } catch (e) {
      setError(e);
    } finally {
      setDemoBusy(false);
    }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!projects) return;
    setBusy(true);
    try {
      let spec: ProjectSpec;
      const src = projects.find((p) => String(p.id) === from);
      if (src) spec = { ...src.spec };
      else {
        const dev = devices.find((d) => d.id === "jetson-orin-nano") ?? devices[0];
        spec = {
          name: "", model_id: from === "example" ? "yolov8s" : models[0]?.id ?? "resnet-50",
          device_id: from === "example" ? "jetson-orin-nx" : dev.id, cooling_id: from === "example" ? "passive_small" : dev.default_cooling,
          baseline: null,
          workload: { fps: 30, ambient_c: 40, hours_per_day: 24, price_per_kwh: 0.15, grid_gco2_per_kwh: 400, streaming: true },
          constraints: { max_power_w: 55, min_accuracy: 0.9, accuracy_mode: "relative", max_latency_ms: null, max_memory_gb: null, forbid_throttling: true },
        } as ProjectSpec;
      }
      spec.name = name.trim() || "Untitled project";
      const p = await api.create(spec);
      navigate(`/p/${p.id}/input`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace">
      {firstVisit ? (
        <section className="hero-start" aria-labelledby="hs-h">
          <h1 id="hs-h">Find out whether your AI model will run well on your hardware, before you build it.</h1>
          <ol className="hero-steps">
            <li><strong>Tell us what you are building</strong> and pick or upload the model and the hardware.</li>
            <li><strong>We predict</strong> speed, memory, power, heat and energy for thousands of settings.</li>
            <li><strong>You get a clear verdict</strong>, the biggest bottleneck and the one change to make, with every engineering number one click away.</li>
          </ol>
          <div className="actions">
            {canWrite && <a className="btn-link primary big" href="#/start">Start a new analysis</a>}
            {projects?.[0] && <a className="btn-link" href={`#/p/${projects[0].id}/input`}>Open the example project</a>}
            {canWrite && <button onClick={addDemo} disabled={demoBusy}>{demoBusy ? "Adding examples…" : "Add five example projects"}</button>}
          </div>
          <p className="hint">Takes about two minutes. Works entirely on this server; no outside service is used.</p>
        </section>
      ) : (
        <header className="ws-head">
          <h1>{ws?.name ?? "Workspace"}</h1>
          <div className="actions">
            {canWrite && <a className="btn-link primary" href="#/start">Start a new analysis</a>}
            {canWrite && <button onClick={addDemo} disabled={demoBusy}>{demoBusy ? "Adding examples…" : "Add example projects"}</button>}
          </div>
        </header>
      )}
      {demoMsg && <p className="note-ok" role="status">{demoMsg}</p>}
      <ErrorNote error={error} onRetry={load} />

      <section className="panel" aria-labelledby="proj-h">
        <div className="row">
          <h2 id="proj-h">Projects</h2>
          <label className="check-row">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
          </label>
        </div>
        {projects === null ? (
          <div className="skeleton" aria-busy="true"><div className="sk-line w90" /><div className="sk-line w75" /></div>
        ) : projects.length === 0 ? (
          <div className="empty-state">
            <p>No projects yet. The guided start creates one and analyses it in about two minutes.</p>
            {canWrite && <a className="btn-link primary" href="#/start">Start a new analysis</a>}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="tbl projects">
              <thead>
                <tr><th>Project</th><th>Today</th><th>Biggest bottleneck</th><th>With the plan</th><th>Last change</th><th><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {projects.map((p) => {
                  const h = healthBy[p.id];
                  return (
                    <tr key={p.id} className={p.archived ? "archived" : ""}>
                      <td>
                        <a href={`#/p/${p.id}/${h?.analysed ? "summary" : "input"}`}>{p.spec.name}</a>
                        {p.archived && <span className="badge"> archived</span>}
                        <small className="block muted">{label(p.spec)}</small>
                      </td>
                      <td>{h?.analysed ? <Outcome state={h.outcome} /> : <span className="muted">Not analysed</span>}
                        {h?.stale && <small className="block warn-text">inputs changed since</small>}</td>
                      <td className="muted">{bottleneckShort(h?.bottleneck)}</td>
                      <td>{h?.best_outcome ? <Outcome state={h.best_outcome} /> : <span className="muted">–</span>}
                        {h?.energy_saving_percent ? <small className="block muted num">−{h.energy_saving_percent.toFixed(0)}% energy</small> : null}</td>
                      <td className="muted">{when(p.updated_at)}</td>
                      <td className="actions-cell">
                        {canWrite && <button className="link" onClick={() => history.archive(p.id, !p.archived).then(load).catch(setError)}>{p.archived ? "Restore" : "Archive"}</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="ws-grid">
        {canWrite && (
          <details className="panel">
            <summary><strong>Create a project manually</strong> <span className="muted">(engineering path: set every input yourself)</span></summary>
            <form onSubmit={create}>
              <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Pick-and-place vision on Orin NX" maxLength={200} /></label>
              <label className="field">Start from
                <select value={from} onChange={(e) => setFrom(e.target.value)}>
                  <option value="example">The robotics example (YOLOv8s, 30 FPS, 55 W, 40 °C)</option>
                  <option value="blank">Library defaults</option>
                  {(projects ?? []).filter((p) => !p.archived).map((p) => <option key={p.id} value={String(p.id)}>A copy of “{p.spec.name}”</option>)}
                </select>
              </label>
              <button className="primary" disabled={busy || projects === null}>{busy ? "Creating…" : "Create project"}</button>
            </form>
          </details>
        )}
        <section className="panel" aria-labelledby="use-h">
          <h2 id="use-h">Usage this month</h2>
          {usage ? (
            <>
              <dl className="kv">
                <dt>Analyses</dt><dd className="num">{usage.this_period.analysis + usage.this_period.plan_analysis}</dd>
                <dt>Local runs</dt><dd className="num">{(usage.this_period.execution_seconds / 60).toFixed(1)} min</dd>
                <dt>Exports</dt><dd className="num">{usage.this_period.export}</dd>
                <dt>Files stored</dt><dd className="num">{fmtBytes(usage.storage_bytes_in_use)}</dd>
                <dt>Platform cost</dt><dd className="num">{usage.platform_cost.total.toFixed(2)} {usage.platform_cost.currency}</dd>
              </dl>
              <p className="hint">{usage.platform_cost.note}</p>
            </>
          ) : (
            <div className="skeleton" aria-busy="true"><div className="sk-line w60" /></div>
          )}
        </section>
      </div>
    </div>
  );
}
