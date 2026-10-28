import type { GraphComponent, SystemGraph } from "../../api";
import type { ExecutionTrace, TraceEvent } from "../types";

/** The 3D map's scene, built from the system graph and one or two execution traces. Pure, so the
 * 3D view and its table fallback always show the same thing.
 *
 * Two layouts, never mixed:
 *   logical   a diagram arrangement from the graph's logical positions (not where parts sit);
 *   physical  board positions in millimetres from a board file (only parts that have them). */

export type Layout = "logical" | "physical";
export type Overlay = "heat" | "activity" | "none";

export interface SceneNode {
  key: string; // unique across systems: `${system}:${id}`
  id: string;
  system: number; // 0 = first trace (today), 1 = second (plan)
  name: string;
  kind: string;
  pos: [number, number, number]; // centre, scene units (y up)
  size: [number, number, number];
  heatW: number | null;
  heatShare: number | null; // 0..1 of the hottest component in this system
  traceComponent: string | null;
  side: "top" | "bottom" | null;
  label: boolean; // gets a text label in 3D
  origin: string;
  confidence: string;
}

export interface SceneEdge {
  key: string;
  system: number;
  from: string; // node key
  to: string;
  kind: string;
  label: string;
  bandwidthGbs: number | null;
  tracePath: boolean; // a memory path of the trace (thickens with use)
}

export interface Scene {
  layout: Layout;
  nodes: SceneNode[];
  edges: SceneEdge[];
  systems: { label: string; offsetX: number; batchMs: number }[];
  extent: number; // half-width of everything, for the camera
  center: [number, number, number];
  note: string;
  missing: string | null; // why the layout has nothing to show, if so
}

const W = 10; // logical plane width in scene units
const D = 7; // logical plane depth
const CORE_H = 0.5;

const KIND_H: Record<string, number> = { board: 0.12, clock: 0.18, logic_module: 0.35, ic: 0.3, passive: 0.12, connector: 0.2 };

function effectiveParents(g: SystemGraph): Map<string, string | null> {
  const parent = new Map(g.components.map((c) => [c.id, c.parent]));
  // Modules without a parent hang under the module that instantiates them.
  for (const e of g.connections) {
    if (e.kind === "instance" && !parent.get(e.target) && e.target !== e.source) parent.set(e.target, e.source);
  }
  return parent;
}

function logicalNodes(g: SystemGraph, t: ExecutionTrace | null, system: number, ox: number): SceneNode[] {
  const heat = new Map((t?.components ?? []).map((c) => [c.id, c]));
  const maxShare = Math.max(0, ...(t?.components ?? []).map((c) => c.heat_share));
  const parent = effectiveParents(g);
  const out = new Map<string, SceneNode>();
  const mk = (c: GraphComponent, pos: [number, number, number], size: [number, number, number], label: boolean): SceneNode => {
    const tc = c.trace_component ? heat.get(c.trace_component) : undefined;
    return {
      key: `${system}:${c.id}`, id: c.id, system, name: c.name, kind: c.kind, pos, size,
      heatW: tc ? tc.heat_w : null, heatShare: tc && maxShare > 0 ? tc.heat_share / maxShare : null,
      traceComponent: c.trace_component, side: null, label, origin: c.origin, confidence: c.confidence,
    };
  };
  // 1. Parts with a logical position (the modelled core), height also encodes heat.
  for (const c of g.components) {
    if (!c.logical || c.kind === "system") continue;
    const L = c.logical;
    const tc = c.trace_component ? heat.get(c.trace_component) : undefined;
    const h = CORE_H + (tc && maxShare > 0 ? 1.2 * (tc.heat_share / maxShare) : 0);
    const x = ox + (L.x + L.w / 2 - 0.5) * W;
    const z = (L.y + L.h / 2 - 0.5) * D;
    out.set(c.id, mk(c, [x, L.z * 0.05 + h / 2, z], [Math.max(L.w * W - 0.25, 0.4), h, Math.max(L.h * D - 0.25, 0.4)], true));
  }
  // 2. Everything else is stacked in a grid on top of its nearest placed ancestor; parts whose
  //    ancestor is the whole system go in a strip in front of the core.
  const children = new Map<string, GraphComponent[]>();
  for (const c of g.components) {
    if (out.has(c.id) || c.kind === "system") continue;
    let p = parent.get(c.id) ?? null;
    const seen = new Set<string>();
    while (p && !out.has(p) && !seen.has(p)) {
      seen.add(p);
      p = parent.get(p) ?? null;
    }
    const k = p && out.has(p) ? p : "__front";
    children.set(k, [...(children.get(k) ?? []), c]);
  }
  for (const [pk, kids] of children) {
    const base = out.get(pk);
    const cols = Math.ceil(Math.sqrt(kids.length));
    const area = base ? [base.size[0] * 0.9, base.size[2] * 0.9] : [W, 1.6];
    const cx = base ? base.pos[0] : ox;
    const cz = base ? base.pos[2] : D / 2 + 1.4;
    const y0 = base ? base.pos[1] + base.size[1] / 2 : 0;
    const rows = Math.ceil(kids.length / cols);
    const cw = area[0] / cols;
    const cd = area[1] / Math.max(rows, 1);
    kids.forEach((c, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const h = KIND_H[c.kind] ?? 0.2;
      out.set(c.id, mk(c, [cx - area[0] / 2 + cw * (col + 0.5), y0 + h / 2 + 0.02, cz - area[1] / 2 + cd * (row + 0.5)],
        [Math.max(cw * 0.8, 0.08), h, Math.max(cd * 0.8, 0.08)], !base && kids.length <= 6));
    });
  }
  return [...out.values()];
}

function physicalNodes(g: SystemGraph, system: number, ox: number): SceneNode[] {
  const placed = g.components.filter((c) => c.physical && c.kind !== "board");
  if (!placed.length) return [];
  const board = g.components.find((c) => c.kind === "board" && c.physical?.width_mm);
  const xs = placed.map((c) => c.physical!.x_mm);
  const ys = placed.map((c) => c.physical!.y_mm);
  const x0 = board?.physical?.x_mm ?? Math.min(...xs) - 5;
  const y0 = board?.physical?.y_mm ?? Math.min(...ys) - 5;
  const bw = board?.physical?.width_mm ?? Math.max(...xs) - x0 + 5;
  const bh = board?.physical?.height_mm ?? Math.max(...ys) - y0 + 5;
  const s = W / Math.max(bw, bh); // scene units per mm
  const nodes: SceneNode[] = [{
    key: `${system}:${board?.id ?? "board"}`, id: board?.id ?? "board", system, name: board ? `${board.name} (${bw.toFixed(1)} × ${bh.toFixed(1)} mm)` : "Board extent (from part positions)",
    kind: "board", pos: [ox, 0, 0], size: [bw * s, 0.1, bh * s], heatW: null, heatShare: null, traceComponent: null, side: null,
    label: true, origin: board?.origin ?? "derived", confidence: board?.confidence ?? "derived",
  }];
  for (const c of placed) {
    const p = c.physical!;
    const side = p.side;
    const size = 0.035 * W;
    nodes.push({
      key: `${system}:${c.id}`, id: c.id, system, name: c.name, kind: c.kind,
      pos: [ox + (p.x_mm - x0 - bw / 2) * s, side === "top" ? 0.05 + size / 2 : -0.05 - size / 2, (p.y_mm - y0 - bh / 2) * s],
      size: [size, size, size], heatW: null, heatShare: null, traceComponent: c.trace_component, side,
      label: placed.length <= 40, origin: c.origin, confidence: c.confidence,
    });
  }
  return nodes;
}

export function buildScene(g: SystemGraph, traces: { label: string; trace: ExecutionTrace | null }[], layout: Layout): Scene {
  const gap = W + 3;
  const n = Math.max(traces.length, 1);
  const systems = (traces.length ? traces : [{ label: "", trace: null }]).map((s, i) => ({
    label: s.label, offsetX: (i - (n - 1) / 2) * gap, batchMs: s.trace?.totals.batch_ms ?? 0,
  }));
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];
  systems.forEach((sys, i) => {
    const t = traces[i]?.trace ?? null;
    const ns = layout === "logical" ? logicalNodes(g, t, i, sys.offsetX) : physicalNodes(g, i, sys.offsetX);
    nodes.push(...ns);
    const keys = new Set(ns.map((x) => x.id));
    const byTrace = new Map(ns.filter((x) => x.traceComponent).map((x) => [x.traceComponent!, x.id]));
    const pathEnds = new Set<string>();
    if (layout === "logical") {
      for (const p of t?.paths ?? []) {
        const a = byTrace.get(p.source);
        const b = byTrace.get(p.target);
        if (a && b) {
          pathEnds.add([a, b].sort().join("|"));
          edges.push({ key: `${i}:path:${p.id}`, system: i, from: `${i}:${a}`, to: `${i}:${b}`, kind: "memory_bus", label: p.label, bandwidthGbs: p.bandwidth_gbs, tracePath: true });
        }
      }
    }
    for (const e of g.connections) {
      if (!keys.has(e.source) || !keys.has(e.target) || e.kind === "contains") continue;
      if (layout === "physical" && e.kind !== "net") continue;
      if (layout === "logical" && e.kind === "net") continue;
      if (pathEnds.has([e.source, e.target].sort().join("|")) && e.kind === "memory_bus") continue;
      const bw = e.attributes.bandwidth?.value;
      edges.push({ key: `${i}:${e.id}`, system: i, from: `${i}:${e.source}`, to: `${i}:${e.target}`, kind: e.kind, label: e.label,
        bandwidthGbs: typeof bw === "number" ? bw : null, tracePath: false });
    }
  });
  const xsMin = Math.min(...nodes.map((x) => x.pos[0] - x.size[0] / 2), -W / 2);
  const xsMax = Math.max(...nodes.map((x) => x.pos[0] + x.size[0] / 2), W / 2);
  const zsMin = Math.min(...nodes.map((x) => x.pos[2] - x.size[2] / 2), -D / 2);
  const zsMax = Math.max(...nodes.map((x) => x.pos[2] + x.size[2] / 2), D / 2);
  const center: [number, number, number] = [(xsMin + xsMax) / 2, 0, (zsMin + zsMax) / 2];
  const extent = Math.max(xsMax - xsMin, zsMax - zsMin) / 2 + 1;
  const physical = layout === "physical";
  return {
    layout, nodes, edges, systems, extent, center,
    note: physical
      ? "Physical layout: positions in millimetres from the board file. Top-side parts sit above the board, bottom-side parts below it (drawn as outlines)."
      : "Logical layout: an arrangement of the modelled parts, not their physical positions. Block height and colour show heat; stacked blocks are the parts and modules inside them.",
    missing: physical && nodes.length === 0 ? "No physical positions yet. Upload a KiCad board (.kicad_pcb) and add it to the hardware graph." : null,
  };
}

/** The event running at time `ms` in a trace (or null once the batch is over). */
export function eventAt(t: ExecutionTrace | null, ms: number): TraceEvent | null {
  if (!t) return null;
  for (const e of t.events) {
    if (ms >= e.start_ms && ms < e.start_ms + e.duration_ms) return e;
  }
  return null;
}
