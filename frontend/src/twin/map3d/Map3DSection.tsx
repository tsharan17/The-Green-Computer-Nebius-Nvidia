import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { type GraphState, systemGraph, twin } from "../../api";
import { ErrorNote, Level } from "../../components/Help";
import type { useTrace } from "../TraceBar";
import type { ExecutionTrace, StoredTrace } from "../types";
import { type Layout, type Overlay, buildScene, eventAt } from "./model";
import { type Camera, type Palette, readPalette } from "./palette";

const Map3DView = lazy(() => import("./Map3DView"));

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

const HOME: Camera = { azimuth: 0.45, elevation: 0.72, distance: 2.5 };
const STEPS = 400;

export default function Map3DSection({ projectId, state, hasBest }: {
  projectId: number;
  state: ReturnType<typeof useTrace>;
  hasBest: boolean;
}) {
  const [graph, setGraph] = useState<GraphState | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [layout, setLayout] = useState<Layout>("logical");
  const [overlay, setOverlay] = useState<Overlay>("heat");
  const [compare, setCompare] = useState(false);
  const [other, setOther] = useState<StoredTrace | null>(null);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [cam, setCam] = useState<Camera>(HOME);
  const [selected, setSelected] = useState<string | null>(null);
  const [tableView, setTableView] = useState(false);
  const [webgl] = useState(hasWebGL);
  const [pal, setPal] = useState<Palette>(readPalette);
  const drag = useRef<{ x: number; y: number; cam: Camera } | null>(null);
  const otherCache = useRef(new Map<string, StoredTrace>());

  useEffect(() => {
    systemGraph.current(projectId).then(setGraph).catch(setError);
  }, [projectId]);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setPal(readPalette());
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // Today vs the plan: fetch whichever trace the page is not already showing.
  const otherWhich = state.which === "baseline" ? "best" : "baseline";
  useEffect(() => {
    if (!compare || !hasBest) return;
    const key = `${projectId}:${otherWhich}`;
    const hit = otherCache.current.get(key);
    if (hit) {
      setOther(hit);
      return;
    }
    twin.create(projectId, otherWhich).then((t) => { otherCache.current.set(key, t); setOther(t); }).catch(setError);
  }, [compare, hasBest, otherWhich, projectId]);

  const t = state.data?.trace ?? null;
  const traces = useMemo(() => {
    const cur = { label: state.which !== "baseline" ? "With the plan" : "Today", trace: t };
    if (!compare || !other) return [cur];
    const o = { label: otherWhich === "best" ? "With the plan" : "Today", trace: other.trace };
    return state.which === "baseline" ? [cur, o] : [o, cur];
  }, [t, compare, other, otherWhich, state.which]);
  const scene = useMemo(() => (graph ? buildScene(graph.graph, traces, layout) : null), [graph, traces, layout]);

  const maxMs = Math.max(...traces.map((x) => x.trace?.totals.batch_ms ?? 0), 0);
  const ms = (step / STEPS) * maxMs;
  const running = traces.map((x) => eventAt(x.trace, ms));
  const activeKeys = useMemo(() => {
    const s = new Set<string>();
    if (!scene) return s;
    running.forEach((ev, i) => {
      if (!ev) return;
      for (const n of scene.nodes) if (n.system === i && n.traceComponent && ev.components.includes(n.traceComponent)) s.add(n.key);
    });
    return s;
  }, [scene, running]);
  const pathUse = useMemo(() => new Map(running.map((ev, i) => [i, ev?.bandwidth_utilisation ?? 0])), [running]);

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setStep((s) => (s >= STEPS ? s : s + 2)), 40);
    return () => window.clearInterval(id);
  }, [playing]);
  useEffect(() => {
    if (playing && step >= STEPS) setPlaying(false);
  }, [playing, step]);

  const rotate = (da: number, de = 0) => setCam((c) => ({ ...c, azimuth: c.azimuth + da, elevation: Math.min(1.45, Math.max(0.08, c.elevation + de)) }));
  const zoom = (f: number) => setCam((c) => ({ ...c, distance: Math.min(4, Math.max(0.6, c.distance * f)) }));

  if (!graph || !scene) {
    return (
      <section className="panel" aria-labelledby="m3-h">
        <h2 id="m3-h">3D map</h2>
        <ErrorNote error={error} />
        {!error && <div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div>}
      </section>
    );
  }

  const sel = scene.nodes.find((n) => n.key === selected) ?? null;
  const comp = (id: string) => graph.graph.components.find((c) => c.id === id);
  const busyText = traces.map((x, i) => {
    const ev = running[i];
    const name = x.label || "This setup";
    if (!x.trace) return `${name}: no trace yet.`;
    return ev ? `${name}: ${ev.name} (${ev.bound}-bound)` : ms >= x.trace.totals.batch_ms ? `${name}: batch finished at ${x.trace.totals.batch_ms.toPrecision(3)} ms` : `${name}: idle`;
  });
  const summary = `${scene.layout === "logical" ? "Logical arrangement" : "Physical board layout"} of ${graph.graph.device_name}, `
    + `${scene.nodes.length} parts${traces.length > 1 ? ", today and with the plan side by side" : ""}.`;
  const show3d = webgl && !tableView && !scene.missing;

  return (
    <section className="panel map3d" aria-labelledby="m3-h">
      <div className="row">
        <h2 id="m3-h">3D map</h2>
        <button className="link" aria-pressed={tableView || !webgl} disabled={!webgl} onClick={() => setTableView((v) => !v)}>
          {tableView || !webgl ? "Show 3D" : "Show as a table"}
        </button>
      </div>
      <p className="hint">{scene.note} Labels and the table carry every value; colour is never the only cue.</p>
      {!webgl && <p className="warn-text" role="status">This browser cannot draw 3D (WebGL is off or unavailable), so the map is shown as a table.</p>}

      <div className="m3-controls">
        <div className="seg" role="radiogroup" aria-label="Layout">
          {(["logical", "physical"] as const).map((l) => (
            <button key={l} role="radio" aria-checked={layout === l} className={layout === l ? "on" : ""} onClick={() => { setLayout(l); setSelected(null); }}>
              {l === "logical" ? "Logical" : "Physical (board)"}
            </button>
          ))}
        </div>
        <div className="seg" role="radiogroup" aria-label="Overlay">
          {(["heat", "activity", "none"] as const).map((o) => (
            <button key={o} role="radio" aria-checked={overlay === o} className={overlay === o ? "on" : ""} onClick={() => setOverlay(o)}>
              {o === "heat" ? "Heat" : o === "activity" ? "Activity" : "Plain"}
            </button>
          ))}
        </div>
        <label className="check-row">
          <input type="checkbox" checked={compare} disabled={!hasBest} onChange={(e) => setCompare(e.target.checked)} />
          Today and the plan side by side
        </label>
      </div>
      {scene.missing && <p className="empty-state">{scene.missing}</p>}

      {!scene.missing && (
        <div className="scrub" aria-label="Playback">
          <button onClick={() => { if (step >= STEPS) setStep(0); setPlaying((p) => !p); if (overlay !== "activity") setOverlay("activity"); }}>
            {playing ? "Pause" : "Play one batch"}
          </button>
          <input type="range" min={0} max={STEPS} value={step} aria-label="Time in the batch"
            aria-valuetext={`${ms.toPrecision(3)} ms. ${busyText.join(" ")}`}
            onChange={(e) => { setPlaying(false); setStep(Number(e.target.value)); }} />
          <span className="num muted">{ms.toPrecision(3)} / {maxMs.toPrecision(3)} ms</span>
        </div>
      )}
      {!scene.missing && <p className="m3-now" aria-live="polite">{busyText.join(" · ")}</p>}

      {show3d && (
        <div className="m3-stage">
          <div className="m3-canvas" tabIndex={0} role="img" aria-label={`${summary} Drag or use the arrow keys to turn it, plus and minus to zoom.`}
            onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, cam }; }}
            onPointerMove={(e) => {
              const d = drag.current;
              if (!d || e.buttons === 0) return;
              setCam({ ...d.cam, azimuth: d.cam.azimuth - (e.clientX - d.x) * 0.008,
                elevation: Math.min(1.45, Math.max(0.08, d.cam.elevation + (e.clientY - d.y) * 0.006)) });
            }}
            onPointerUp={() => { drag.current = null; }}
            onWheel={(e) => zoom(e.deltaY > 0 ? 1.08 : 0.93)}
            onKeyDown={(e) => {
              const k: Record<string, () => void> = { ArrowLeft: () => rotate(0.12), ArrowRight: () => rotate(-0.12), ArrowUp: () => rotate(0, 0.08),
                ArrowDown: () => rotate(0, -0.08), "+": () => zoom(0.9), "=": () => zoom(0.9), "-": () => zoom(1.1), "0": () => setCam(HOME) };
              if (k[e.key]) { e.preventDefault(); k[e.key](); }
            }}>
            <Suspense fallback={<div className="skeleton" aria-busy="true"><div className="sk-line w60" /></div>}>
              <Map3DView scene={scene} overlay={overlay} activeKeys={activeKeys} pathUse={pathUse} selected={selected}
                onSelect={setSelected} cam={cam} pal={pal} />
            </Suspense>
          </div>
          <div className="m3-side">
            <div className="m3-view-buttons" role="group" aria-label="View">
              <button onClick={() => rotate(0.3)} aria-label="Turn left">⟲</button>
              <button onClick={() => rotate(-0.3)} aria-label="Turn right">⟳</button>
              <button onClick={() => zoom(0.85)} aria-label="Zoom in">+</button>
              <button onClick={() => zoom(1.15)} aria-label="Zoom out">−</button>
              <button onClick={() => setCam(HOME)}>Reset</button>
            </div>
            {sel ? (
              <dl className="kv m3-detail" aria-live="polite">
                <dt>Part</dt><dd>{sel.name}</dd>
                <dt>Kind</dt><dd>{sel.kind.replace("_", " ")}</dd>
                {traces.length > 1 && <><dt>Setup</dt><dd>{traces[sel.system]?.label}</dd></>}
                {sel.heatW !== null && <><dt>Heat</dt><dd className="num">{sel.heatW.toPrecision(3)} W ({traces[sel.system]?.trace?.kind ?? "predicted"})</dd></>}
                {sel.side && <><dt>Board side</dt><dd>{sel.side}</dd></>}
                <dt>Source</dt><dd>{sel.origin}</dd>
                <dt>Confidence</dt><dd>{sel.confidence}</dd>
                <dt>Busy now</dt><dd>{activeKeys.has(sel.key) ? running[sel.system]?.name ?? "–" : "no"}</dd>
              </dl>
            ) : (
              <p className="hint">Click a block to see what it is and where its facts came from.</p>
            )}
            <ul className="m3-legend">
              {overlay === "heat" && scene.layout === "logical" && <li><span className="sw heat" aria-hidden /> Warmer colour and taller block = more heat (watts in the label)</li>}
              {overlay === "activity" && <li><span className="sw act" aria-hidden /> Outlined and glowing = busy in the running operator (“busy” in the label)</li>}
              {scene.layout === "logical" && <li><span className="sw path" aria-hidden /> Memory path: thicker = more of its bandwidth in use</li>}
              {scene.layout === "physical" && <li>■ solid = top side, ▢ outline = bottom side. Lines are nets between parts.</li>}
              {scene.layout === "physical" && overlay !== "none" && <li>Heat and activity are known for the modelled parts only, so they are shown in the logical layout.</li>}
            </ul>
          </div>
        </div>
      )}

      {(tableView || !webgl) && !scene.missing && (
        <div className="table-wrap">
          <table className="tbl m3-table">
            <caption className="sr-only">{summary}</caption>
            <thead><tr>
              {traces.length > 1 && <th>Setup</th>}<th>Part</th><th>Kind</th>
              <th>{scene.layout === "physical" ? "Position on the board" : "Inside"}</th><th className="num">Heat (W)</th><th>Busy now</th><th>Source</th>
            </tr></thead>
            <tbody>
              {scene.nodes.map((n) => {
                const c = comp(n.id);
                const where = scene.layout === "physical"
                  ? c?.physical ? `${c.physical.x_mm} mm, ${c.physical.y_mm} mm, ${c.physical.side}` : "board"
                  : c?.parent ? comp(c.parent)?.name ?? c.parent : "–";
                return (
                  <tr key={n.key}>
                    {traces.length > 1 && <td>{traces[n.system]?.label}</td>}
                    <td>{n.name}</td><td>{n.kind.replace("_", " ")}</td><td>{where}</td>
                    <td className="num">{n.heatW !== null ? n.heatW.toPrecision(3) : "–"}</td>
                    <td>{activeKeys.has(n.key) ? `yes: ${running[n.system]?.name}` : "no"}</td>
                    <td>{n.origin}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Level kind="explain" title="How to read the 3D map">
        <p>Each block is a part of the system. In the logical layout the modelled parts (compute, memory, power,
          cooling) are arranged as on the 2D system map, and the parts, logic modules and clocks read from your
          design files sit on top of the block they belong to. The physical layout appears once a KiCad board has
          been added to the hardware graph; it places each part where the board file puts it.</p>
        <p>Play one batch to watch the operators run; with “today and the plan” both setups play on the same clock,
          so the faster one finishes first. Heat values come from the {traces[0]?.trace?.kind ?? "predicted"} trace;
          nothing in this view is measured unless the trace says so.</p>
      </Level>
      <MetaNote traces={traces} />
    </section>
  );
}

function MetaNote({ traces }: { traces: { label: string; trace: ExecutionTrace | null }[] }) {
  return (
    <Level kind="engineering" title="Trace behind the map">
      <ul>
        {traces.map((x) => x.trace && (
          <li key={x.label}>{x.label}: {x.trace.kind} trace ({x.trace.provenance}), {x.trace.config.precision as string} batch {x.trace.batch},
            batch time {x.trace.totals.batch_ms.toPrecision(3)} ms, {x.trace.events.length} events. {x.trace.fidelity_note}</li>
        ))}
      </ul>
    </Level>
  );
}
