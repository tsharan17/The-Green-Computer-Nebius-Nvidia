import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type GraphAttr,
  type GraphComponent,
  type GraphDiff,
  type GraphSourceFile,
  type GraphState,
  type GraphVersion,
  systemGraph,
} from "../api";
import { ErrorNote, Level } from "../components/Help";

const KIND_LABEL: Record<string, string> = {
  system: "System", board: "Board", compute: "Compute", memory: "Memory", storage: "Storage", link: "Link",
  power: "Power", cooling: "Cooling", ic: "Chip", passive: "Passive part", discrete: "Discrete part",
  connector: "Connector", clock: "Clock", logic_module: "Logic module", sensor: "Sensor", other: "Other",
};
const CONF_LABEL: Record<string, string> = {
  stated: "Stated", parsed: "Read from file", derived: "Derived", assumed: "Assumed", measured: "Measured",
};
const TOOL_LABEL: Record<string, string> = {
  kicad_sch: "KiCad schematic", kicad_pcb: "KiCad board", verilog: "Verilog", vivado_timing: "Vivado timing",
  vivado_util: "Vivado utilisation", vivado_power: "Vivado power", hls_report: "HLS report",
};

function fmt(a: GraphAttr) {
  const v = typeof a.value === "number" ? (Number.isInteger(a.value) ? a.value.toLocaleString() : String(Number(a.value.toPrecision(4)))) : String(a.value);
  return a.unit ? `${v} ${a.unit}` : v;
}

function originLabel(origin: string) {
  const [kind, rest] = [origin.split(":")[0], origin.split(":").slice(1).join(":")];
  if (kind === "library") return `Library profile (${rest})`;
  if (kind === "profile") return `Your profile (${rest})`;
  if (kind === "user") return "Entered by hand";
  return `${TOOL_LABEL[kind] ?? kind}: ${rest}`;
}

/** Where parts sit on the board, in millimetres, from a KiCad board file. Top-side parts are filled
 * squares, bottom-side parts hollow circles (shape, not colour, tells the side). */
function BoardView({ comps }: { comps: GraphComponent[] }) {
  const board = comps.find((c) => c.kind === "board" && c.physical?.width_mm);
  const parts = comps.filter((c) => c.physical && c.kind !== "board");
  if (!parts.length) return null;
  const xs = parts.map((p) => p.physical!.x_mm);
  const ys = parts.map((p) => p.physical!.y_mm);
  const x0 = board?.physical ? board.physical.x_mm : Math.min(...xs) - 5;
  const y0 = board?.physical ? board.physical.y_mm : Math.min(...ys) - 5;
  const w = board?.physical?.width_mm ?? Math.max(...xs) - x0 + 5;
  const h = board?.physical?.height_mm ?? Math.max(...ys) - y0 + 5;
  const pad = Math.max(w, h) * 0.04;
  return (
    <figure className="board-view">
      <svg viewBox={`${x0 - pad} ${y0 - pad} ${w + 2 * pad} ${h + 2 * pad}`} role="img"
        aria-label={`Board ${w.toFixed(0)} by ${h.toFixed(0)} millimetres with ${parts.length} placed parts`}>
        <rect x={x0} y={y0} width={w} height={h} className="bv-board" />
        {parts.map((p) => {
          const { x_mm: x, y_mm: y, side } = p.physical!;
          const r = Math.max(w, h) * 0.012;
          return (
            <g key={p.id}>
              {side === "top" ? <rect x={x - r} y={y - r} width={2 * r} height={2 * r} className="bv-part top" />
                : <circle cx={x} cy={y} r={r} className="bv-part bottom" />}
              <text x={x + r * 1.4} y={y + r * 0.6} className="bv-label" fontSize={r * 2.4}>{p.id.replace(/^part:/, "")}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="hint">
        Physical layout from the board file, in millimetres{board ? ` (${w.toFixed(1)} × ${h.toFixed(1)} mm)` : ""}.
        ■ top side, ○ bottom side. Positions only: copper, current and heat spreading are not analysed.
      </figcaption>
    </figure>
  );
}

function AttrTable({ c }: { c: GraphComponent }) {
  const rows = Object.entries(c.attributes);
  if (!rows.length) return <p className="muted">No recorded facts.</p>;
  return (
    <table className="tbl attrs">
      <thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Confidence</th></tr></thead>
      <tbody>
        {rows.map(([k, a]) => (
          <tr key={k}>
            <td>{k.replace(/_(ns|w|c)$/, "").replace(/_/g, " ")}</td>
            <td className="num">{fmt(a)}{a.note && <small className="block muted">{a.note}</small>}</td>
            <td>{originLabel(a.origin)}</td>
            <td><span className={`conf ${a.confidence}`}>{CONF_LABEL[a.confidence]}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function HardwareGraph({ projectId }: { projectId: number }) {
  const [state, setState] = useState<GraphState | null>(null);
  const [versions, setVersions] = useState<GraphVersion[]>([]);
  const [files, setFiles] = useState<GraphSourceFile[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [shown, setShown] = useState<number | null>(null);
  const [diff, setDiff] = useState<GraphDiff | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (v: number | null = null) => {
    try {
      const [cur, vs, fs] = await Promise.all([
        v === null ? systemGraph.current(projectId) : systemGraph.version(projectId, v),
        systemGraph.versions(projectId), systemGraph.sources(projectId),
      ]);
      setState(cur);
      setVersions(vs);
      setFiles(fs);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [projectId]);
  useEffect(() => { load(shown); }, [load, shown]);

  async function addFiles() {
    setBusy(true);
    try {
      const v = await systemGraph.fromFiles(projectId, picked);
      setPicked([]);
      const d = await systemGraph.diff(projectId, v.version - 1, v.version);
      setDiff(d);
      setShown(null);
      await load(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const g = state?.graph;
  const byId = useMemo(() => new Map((g?.components ?? []).map((c) => [c.id, c])), [g]);
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (g?.components ?? []).filter((c) => !q || `${c.id} ${c.name} ${c.kind}`.toLowerCase().includes(q));
  }, [g, filter]);
  if (!state || !g) return <section className="panel"><h2>Hardware graph</h2><ErrorNote error={error} onRetry={() => load(shown)} />{!error && <div className="skeleton" aria-busy="true"><div className="sk-line w75" /></div>}</section>;

  const fromFiles = g.sources.filter((s) => s.artifact_id !== null);
  const counts = g.components.reduce<Record<string, number>>((m, c) => ({ ...m, [c.kind]: (m[c.kind] ?? 0) + 1 }), {});
  const links = state.links;
  const fresh = new Set(files.filter((f) => !(state.source_artifacts ?? []).includes(f.id) && !fromFiles.some((s) => s.artifact_id === f.id)).map((f) => f.id));

  return (
    <section className="panel hw-graph" aria-labelledby="hg-h">
      <div className="row">
        <h2 id="hg-h">Hardware graph</h2>
        <label className="field inline">Version
          <select value={shown ?? ""} onChange={(e) => { setDiff(null); setShown(e.target.value === "" ? null : Number(e.target.value)); }}>
            <option value="">{versions.length ? `Latest (v${versions[0].version})` : "From the device profile (not saved)"}</option>
            {versions.slice(1).map((v) => <option key={v.version} value={v.version}>v{v.version}: {v.note}</option>)}
          </select>
        </label>
      </div>
      <p className="hg-summary">
        {g.device_name}: <strong>{g.components.length} parts</strong> and <strong>{g.connections.length} connections</strong>
        {fromFiles.length ? `, from the device profile and ${fromFiles.length} design file${fromFiles.length === 1 ? "" : "s"}` : ", from the device profile"}.
        {" "}Every fact names the file it came from.
      </p>
      {state.outdated && <p className="warn-text" role="status">⚠ This graph was saved for another device than the project now uses. Adding files starts a new graph from the current device.</p>}
      <ErrorNote error={error} />

      <div className="hg-add">
        <h3>Add uploaded design files</h3>
        {files.length === 0 ? (
          <p className="hint">Upload a KiCad schematic or board, Verilog source, a Vivado timing/utilisation/power report or an HLS synthesis report under “Model and hardware”, then add it here.</p>
        ) : (
          <>
            <ul className="file-picks">
              {files.map((f) => (
                <li key={f.id}>
                  <label className="check-row">
                    <input type="checkbox" checked={picked.includes(f.id)}
                      onChange={(e) => setPicked((p) => (e.target.checked ? [...p, f.id] : p.filter((x) => x !== f.id)))} />
                    {f.filename} <span className="muted">({TOOL_LABEL[f.tool] ?? f.tool}{fresh.has(f.id) ? "" : ", already in the graph"})</span>
                  </label>
                </li>
              ))}
            </ul>
            <button className="primary" disabled={!picked.length || busy} onClick={addFiles}>{busy ? "Adding…" : `Add ${picked.length || ""} to the graph`}</button>
          </>
        )}
      </div>

      {diff && (
        <div className="note-ok" role="status">
          Saved as version {diff.to}: {diff.components_added.length} parts and {diff.connections_added.length} connections added,
          {" "}{diff.attributes_changed.length} facts added or changed.
        </div>
      )}

      <Level kind="explain" title="What the graph holds">
        <ul className="kind-counts">
          {Object.entries(counts).map(([k, n]) => <li key={k}><strong className="num">{n}</strong> {KIND_LABEL[k] ?? k}</li>)}
        </ul>
        <p className="hint">
          Parts the analysis models (compute, memory, power, cooling) link to the execution trace
          {links ? ` (${links.kind === "measured" ? "measured" : "predicted"} trace #${links.trace_id})` : "; create a trace on this page to see the links"}.
          Parts read from design files are recorded as stated there. {g.limitations[0]}
        </p>
        <BoardView comps={g.components} />
        {g.warnings.length > 0 && (
          <details className="why"><summary>{g.warnings.length} note{g.warnings.length === 1 ? "" : "s"} from the files</summary>
            <ul>{g.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </details>
        )}
      </Level>

      <Level kind="engineering" title="Every part, connection and fact with its source">
        <label className="field inline">Filter<input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="e.g. U2, clock, rtl" /></label>
        <div className="table-wrap">
          <table className="tbl hg-parts">
            <thead><tr><th>Part</th><th>Kind</th><th>Inside</th><th>Source</th><th>Confidence</th><th className="num">Trace events</th><th><span className="sr-only">Details</span></th></tr></thead>
            <tbody>
              {rows.slice(0, 400).map((c) => {
                const l = c.trace_component ? links?.components[c.trace_component] : undefined;
                return [
                  <tr key={c.id}>
                    <td><strong>{c.name}</strong><small className="block muted">{c.id}</small></td>
                    <td>{KIND_LABEL[c.kind] ?? c.kind}</td>
                    <td>{c.parent ? byId.get(c.parent)?.name ?? c.parent : "–"}</td>
                    <td>{originLabel(c.origin)}</td>
                    <td><span className={`conf ${c.confidence}`}>{CONF_LABEL[c.confidence]}</span></td>
                    <td className="num">{l ? `${l.events} (${l.busy_ms.toPrecision(3)} ms)` : "–"}</td>
                    <td><button className="link" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
                      {open === c.id ? "Hide" : `${Object.keys(c.attributes).length} facts`}</button></td>
                  </tr>,
                  open === c.id && <tr key={`${c.id}-facts`} className="facts-row"><td colSpan={7}><AttrTable c={c} /></td></tr>,
                ];
              })}
            </tbody>
          </table>
        </div>
        {rows.length > 400 && <p className="hint">Showing 400 of {rows.length}; use the filter to narrow it.</p>}
        <h3>Connections ({g.connections.length})</h3>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>From</th><th>To</th><th>Kind</th><th>Label</th><th>Source</th></tr></thead>
            <tbody>
              {g.connections.slice(0, 300).map((e) => (
                <tr key={e.id}>
                  <td>{byId.get(e.source)?.name ?? e.source}</td><td>{byId.get(e.target)?.name ?? e.target}</td>
                  <td>{e.kind.replace("_", " ")}</td><td>{e.label}</td><td>{originLabel(e.origin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Limitations</h3>
        <ul>{g.limitations.map((x) => <li key={x}>{x}</li>)}</ul>
      </Level>
    </section>
  );
}
