"""Build and merge hardware system graphs. Pure: profiles and parsed design files in, graph out.

`base_graph` turns a device profile into the parts the analysis models (compute, memory, power,
cooling; flash and on-chip memory when present), each linked to the trace component of the same
role. `merge_parsed` adds what a parsed design file states (KiCad parts and placement, Verilog
modules and instances, Vivado and HLS figures) without ever replacing a fact silently: a new value
for an existing attribute is kept alongside the old one with its own origin.
"""

from __future__ import annotations

from typing import Any

from app.schemas.hardware import HardwareProfile
from app.schemas.system_graph import (
    Attr,
    Confidence,
    GraphComponent,
    GraphConnection,
    GraphSource,
    LogicalLayout,
    PhysicalPlacement,
    SystemGraph,
)

LIMITATIONS = [
    "The graph records what profiles and files state. No electrical, thermal, timing or functional "
    "simulation was run on uploaded design files by this platform.",
    "Timing figures appear only when a Vivado timing report was uploaded; they are Vivado's own analysis.",
    "HLS figures are pre-implementation estimates from the HLS tool.",
]  # fmt: skip


def _a(value: Any, origin: str, conf: Confidence, unit: str | None = None, art: int | None = None, note: str = "") -> Attr:
    return Attr(value=value, unit=unit, origin=origin, confidence=conf, artifact_id=art, note=note)


def base_graph(hw: HardwareProfile, power_mode_id: str | None = None, cooling_id: str | None = None) -> SystemGraph:
    origin = f"library:{hw.id}" if not hw.id.startswith("custom") else f"profile:{hw.id}"
    conf: Confidence = "measured" if hw.verified else "stated"
    mode = hw.mode(power_mode_id) if power_mode_id else hw.power_modes[0]
    cool = hw.cooling(cooling_id or hw.default_cooling)
    comps = [
        GraphComponent(id="system", name=hw.name, kind="system", origin=origin, confidence=conf,
                       attributes={"vendor": _a(hw.vendor, origin, conf), "kind": _a(hw.kind, origin, conf),
                                   "throttle_c": _a(hw.t_throttle_c, origin, conf, "°C"),
                                   "shutdown_c": _a(hw.t_shutdown_c, origin, conf, "°C")},
                       logical=LogicalLayout(x=0, y=0, w=1, h=1, z=0)),
        GraphComponent(id="compute", name="Compute engine", kind="compute", parent="system", origin=origin,
                       confidence=conf, trace_component="compute",
                       attributes={f"peak_{p}": _a(v, origin, conf, "TOPS" if p == "int8" else "TFLOPS")
                                   for p, v in hw.peak_tflops.items()}
                       | {"dynamic_power_max": _a(hw.dyn_w_max, origin, conf, "W")},
                       logical=LogicalLayout(x=0.30, y=0.32, w=0.36, h=0.30, z=1)),
        GraphComponent(id="ram", name="SRAM" if hw.kind == "mcu" else "Main memory", kind="memory", parent="system",
                       origin=origin, confidence=conf, trace_component="ram",
                       attributes={"capacity": _a(hw.mem_gb, origin, conf, "GB"),
                                   "bandwidth": _a(hw.mem_bw_gbs, origin, conf, "GB/s"),
                                   "power_max": _a(hw.mem_w_max, origin, conf, "W")},
                       logical=LogicalLayout(x=0.71, y=0.32, w=0.29, h=0.30, z=1)),
        GraphComponent(id="power", name="Power delivery", kind="power", parent="system", origin=origin,
                       confidence=conf, trace_component="power",
                       attributes={"mode": _a(mode.name, origin, conf), "cap": _a(mode.cap_w, origin, conf, "W"),
                                   "idle": _a(hw.idle_w, origin, conf, "W")},
                       logical=LogicalLayout(x=0.30, y=0.76, w=0.36, h=0.22, z=0)),
        GraphComponent(id="cooling", name=cool.name, kind="cooling", parent="system", origin=origin,
                       confidence=conf, trace_component="cooling",
                       attributes={"thermal_resistance": _a(cool.r_th_k_per_w, origin, conf, "K/W"),
                                   "fan_power": _a(cool.fan_w, origin, conf, "W")},
                       logical=LogicalLayout(x=0.30, y=0.0, w=0.36, h=0.22, z=2)),
    ]  # fmt: skip
    conns = [
        GraphConnection(id="compute-ram", source="compute", target="ram", kind="memory_bus", label="memory bus",
                        attributes={"bandwidth": _a(hw.mem_bw_gbs, origin, conf, "GB/s")}, origin=origin, confidence=conf),
        GraphConnection(id="power-compute", source="power", target="compute", kind="power", label="supply",
                        origin=origin, confidence=conf),
        GraphConnection(id="compute-cooling", source="compute", target="cooling", kind="thermal", label="heat path",
                        attributes={"thermal_resistance": _a(cool.r_th_k_per_w, origin, conf, "K/W")},
                        origin=origin, confidence=conf),
    ]  # fmt: skip
    if hw.weights_mem_gb:
        comps.append(GraphComponent(id="flash", name="Flash (weights)", kind="storage", parent="system", origin=origin,
                                    confidence=conf, trace_component="flash",
                                    attributes={"capacity": _a(hw.weights_mem_gb, origin, conf, "GB")},
                                    logical=LogicalLayout(x=0.71, y=0.70, w=0.29, h=0.28, z=1)))  # fmt: skip
        conns.append(GraphConnection(id="compute-flash", source="compute", target="flash", kind="memory_bus",
                                     label="weights", origin=origin, confidence=conf))  # fmt: skip
    if hw.onchip_weights_mb:
        attrs = {"capacity": _a(hw.onchip_weights_mb, origin, conf, "MB")}
        if hw.onchip_bw_gbs:
            attrs["bandwidth"] = _a(hw.onchip_bw_gbs, origin, conf, "GB/s")
        comps.append(GraphComponent(id="onchip", name="On-chip memory", kind="memory", parent="system", origin=origin,
                                    confidence=conf, trace_component="onchip", attributes=attrs,
                                    logical=LogicalLayout(x=0.0, y=0.32, w=0.26, h=0.30, z=1)))  # fmt: skip
        conns.append(GraphConnection(id="compute-onchip", source="compute", target="onchip", kind="memory_bus",
                                     label="on-chip", origin=origin, confidence=conf))  # fmt: skip
    return SystemGraph(device_id=hw.id, device_name=hw.name, components=comps, connections=conns,
                       sources=[GraphSource(origin=origin, components=len(comps), connections=len(conns))],
                       limitations=list(LIMITATIONS))  # fmt: skip


class _Merge:
    def __init__(self, g: SystemGraph, origin: str, art: int | None, filename: str | None) -> None:
        self.g = g.model_copy(deep=True)
        self.by_id = {c.id: c for c in self.g.components}
        self.conn_ids = {c.id for c in self.g.connections}
        self.origin, self.art = origin, art
        self.src = GraphSource(origin=origin, artifact_id=art, filename=filename)

    def comp(self, c: GraphComponent) -> GraphComponent:
        have = self.by_id.get(c.id)
        if have is None:
            self.g.components.append(c)
            self.by_id[c.id] = c
            self.src.components += 1
            return c
        for k, v in c.attributes.items():
            self.attr(have, k, v)
        if c.physical and not have.physical:
            have.physical = c.physical
        return have

    def attr(self, c: GraphComponent, key: str, a: Attr) -> None:
        old = c.attributes.get(key)
        if old is not None and old.value != a.value and old.origin != a.origin:
            key = f"{key} ({a.origin.split(':')[0]})"  # keep both, each with its own origin
        c.attributes[key] = a
        self.src.attributes += 1

    def conn(self, c: GraphConnection) -> None:
        if c.id in self.conn_ids or c.source not in self.by_id or c.target not in self.by_id:
            return
        self.g.connections.append(c)
        self.conn_ids.add(c.id)
        self.src.connections += 1

    def a(self, value: Any, unit: str | None = None, conf: Confidence = "parsed", note: str = "") -> Attr:
        return _a(value, self.origin, conf, unit, self.art, note)

    def done(self, warnings: list[str]) -> SystemGraph:
        self.g.sources.append(self.src)
        self.g.warnings = [*self.g.warnings, *(f"{self.src.filename or self.origin}: {w}" for w in warnings)]
        return self.g


def _board(m: _Merge) -> GraphComponent:
    return m.comp(GraphComponent(id="board", name="Board", kind="board", parent="system", origin=m.origin,
                                 confidence="parsed", artifact_id=m.art))  # fmt: skip


def _part_id(ref: str) -> str:
    return f"part:{ref}"


def merge_parsed(g: SystemGraph, parsed: dict[str, Any], artifact_id: int | None = None,
                 filename: str | None = None) -> SystemGraph:  # fmt: skip
    """Add what a parsed design file states. Unknown tools leave the graph unchanged."""
    tool = parsed.get("tool")
    m = _Merge(g, f"{tool}:{filename or artifact_id}", artifact_id, filename)
    if tool == "kicad_sch":
        board = _board(m)
        t = parsed.get("title") or {}
        if t.get("title"):
            m.attr(board, "schematic_title", m.a(t["title"]))
        if t.get("rev"):
            m.attr(board, "schematic_rev", m.a(t["rev"]))
        for p in parsed.get("parts", []):
            attrs: dict[str, Attr] = {"value": m.a(p["value"]), "symbol": m.a(p["lib_id"])}
            if p.get("footprint"):
                attrs["footprint"] = m.a(p["footprint"])
            m.comp(GraphComponent(id=_part_id(p["ref"]), name=f"{p['ref']} {p['value']}".strip(), kind=p["kind"],
                                  parent="board", attributes=attrs, origin=m.origin, confidence="parsed",
                                  artifact_id=m.art))  # fmt: skip
    elif tool == "kicad_pcb":
        board = _board(m)
        o = parsed.get("outline")
        if o:
            m.attr(board, "width", m.a(o["width_mm"], "mm"))
            m.attr(board, "height", m.a(o["height_mm"], "mm"))
            board.physical = board.physical or PhysicalPlacement(x_mm=o["x_mm"], y_mm=o["y_mm"],
                                                                  width_mm=o["width_mm"], height_mm=o["height_mm"])  # fmt: skip
        if parsed.get("thickness_mm") is not None:
            m.attr(board, "thickness", m.a(parsed["thickness_mm"], "mm"))
        m.attr(board, "copper_layers", m.a(len(parsed.get("copper_layers", []))))
        m.attr(board, "nets", m.a(parsed.get("net_count", 0)))
        for p in parsed.get("parts", []):
            place = (PhysicalPlacement(x_mm=p["x_mm"], y_mm=p["y_mm"], rotation_deg=p.get("rotation") or 0.0,
                                       side=p.get("side", "top")) if p.get("x_mm") is not None and p.get("y_mm") is not None else None)  # fmt: skip
            m.comp(GraphComponent(id=_part_id(p["ref"]), name=f"{p['ref']} {p['value']}".strip(), kind=p["kind"],
                                  parent="board", attributes={"value": m.a(p["value"]), "footprint": m.a(p["footprint"])},
                                  origin=m.origin, confidence="parsed", artifact_id=m.art, physical=place))  # fmt: skip
        for c in parsed.get("connections", []):
            m.conn(GraphConnection(id=f"net:{c['net']}:{c['a']}:{c['b']}", source=_part_id(c["a"]),
                                   target=_part_id(c["b"]), kind="net", label=c["net"], origin=m.origin,
                                   confidence="parsed", artifact_id=m.art))  # fmt: skip
    elif tool == "verilog":
        parent = "compute"
        for md in parsed.get("modules", []):
            widths = [p["width"] for p in md["ports"] if isinstance(p["width"], int)]
            attrs = {"ports": m.a(md["port_count"]), "instances": m.a(len(md["instances"]))}
            if widths:
                attrs["port_bits"] = m.a(sum(widths), "bits", note="sum of port widths with numeric ranges")
            if md["clocks"]:
                attrs["clock_inputs"] = m.a(", ".join(md["clocks"]))
            for k, v in list(md["parameters"].items())[:20]:
                attrs[f"param {k}"] = m.a(v)
            is_top = md["name"] in parsed.get("top_modules", [])
            m.comp(GraphComponent(id=f"rtl:{md['name']}", name=md["name"], kind="logic_module",
                                  parent=parent if is_top else None, attributes=attrs, origin=m.origin,
                                  confidence="parsed", artifact_id=m.art))  # fmt: skip
        for name in parsed.get("external_modules", [])[:200]:
            m.comp(GraphComponent(id=f"rtl:{name}", name=name, kind="logic_module", origin=m.origin,
                                  confidence="assumed", artifact_id=m.art,
                                  attributes={"defined": m.a(False, conf="assumed", note="instantiated but not in the uploaded files")}))  # fmt: skip
        for md in parsed.get("modules", []):
            for inst in md["instances"]:
                m.conn(GraphConnection(id=f"inst:{md['name']}:{inst['name']}", source=f"rtl:{md['name']}",
                                       target=f"rtl:{inst['module']}", kind="instance", label=inst["name"],
                                       origin=m.origin, confidence="parsed", artifact_id=m.art))  # fmt: skip
    elif tool == "vivado_util":
        comp = m.by_id["compute"]
        for k, v in (parsed.get("found") or {}).items():
            m.attr(comp, f"{k}_used", m.a(v["used"]))
            m.attr(comp, f"{k}_available", m.a(v["available"]))
            if v.get("available"):
                m.attr(comp, f"{k}_utilisation", m.a(round(100 * v["used"] / v["available"], 2), "%", "derived"))
    elif tool == "vivado_power":
        fields = {**(parsed.get("fields") or {}), **(parsed.get("found") or {})}
        units = {"dynamic_w": "W", "static_w": "W", "total_w": "W", "junction_c": "°C", "theta_ja_k_per_w": "K/W"}
        target = {"theta_ja_k_per_w": "cooling", "junction_c": "compute"}
        for k, v in fields.items():
            m.attr(m.by_id[target.get(k, "power")], f"vivado_{k}", m.a(v, units.get(k), note="Vivado power estimate"))
    elif tool == "vivado_timing":
        comp = m.by_id["compute"]
        for k, v in (parsed.get("summary") or {}).items():
            m.attr(comp, f"timing_{k}", m.a(v, "ns" if k.endswith("_ns") else None, note="Vivado static timing analysis"))
        m.attr(comp, "timing_met", m.a(bool(parsed.get("constraints_met")), note="as reported by Vivado"))
        for c in parsed.get("clocks", [])[:32]:
            cid = f"clock:{c['name']}"
            m.comp(GraphComponent(id=cid, name=c["name"], kind="clock", parent="compute", origin=m.origin,
                                  confidence="parsed", artifact_id=m.art,
                                  attributes={"period": m.a(c["period_ns"], "ns"), "frequency": m.a(c["frequency_mhz"], "MHz")}))  # fmt: skip
            m.conn(GraphConnection(id=f"clk:{c['name']}", source=cid, target="compute", kind="clock",
                                   label=c["name"], origin=m.origin, confidence="parsed", artifact_id=m.art))  # fmt: skip
    elif tool == "hls_report":
        top = parsed.get("top") or "hls_top"
        attrs = {}
        lat = parsed.get("latency_cycles") or {}
        if lat.get("best") is not None:
            attrs["latency_best"] = m.a(lat["best"], "cycles")
        if lat.get("worst") is not None:
            attrs["latency_worst"] = m.a(lat["worst"], "cycles")
        itv = parsed.get("interval_cycles") or {}
        if itv.get("min") is not None:
            attrs["interval_min"] = m.a(itv["min"], "cycles")
        for k in ("target_clock_ns", "estimated_clock_ns"):
            if parsed.get(k) is not None:
                attrs[k.replace("_ns", "")] = m.a(parsed[k], "ns", note="HLS estimate")
        if parsed.get("latency_us_worst") is not None:
            attrs["latency_worst_time"] = m.a(parsed["latency_us_worst"], "µs", "derived")
        for k, v in (parsed.get("resources") or {}).items():
            attrs[f"{k} used"] = m.a(v)
        m.comp(GraphComponent(id=f"rtl:{top}", name=top, kind="logic_module", parent="compute", attributes=attrs,
                              origin=m.origin, confidence="parsed", artifact_id=m.art))  # fmt: skip
    return m.done(list(parsed.get("warnings", [])))


def diff(a: SystemGraph, b: SystemGraph) -> dict[str, Any]:
    """What changed from graph a to graph b: components and connections added/removed, attributes changed."""
    ca, cb = {c.id: c for c in a.components}, {c.id: c for c in b.components}
    na, nb = {c.id for c in a.connections}, {c.id for c in b.connections}
    changed = []
    for cid in sorted(ca.keys() & cb.keys()):
        x, y = ca[cid].attributes, cb[cid].attributes
        for k in sorted(x.keys() | y.keys()):
            vx, vy = (x[k].value if k in x else None), (y[k].value if k in y else None)
            if vx != vy:
                changed.append({"component": cid, "attribute": k, "from": vx, "to": vy})
    return {"components_added": sorted(cb.keys() - ca.keys()), "components_removed": sorted(ca.keys() - cb.keys()),
            "connections_added": sorted(nb - na), "connections_removed": sorted(na - nb), "attributes_changed": changed}  # fmt: skip


def validate(g: SystemGraph) -> list[str]:
    """Structural problems: duplicate ids, dangling parents or connection ends, parent cycles."""
    errs: list[str] = []
    ids = [c.id for c in g.components]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append("duplicate component ids: " + ", ".join(sorted(dup)[:10]))
    known = set(ids)
    for c in g.components:
        if c.parent is not None and c.parent not in known:
            errs.append(f"{c.id}: parent {c.parent} does not exist")
    for e in g.connections:
        if e.source not in known or e.target not in known:
            errs.append(f"connection {e.id}: both ends must be components in the graph")
    parent = {c.id: c.parent for c in g.components}
    for c in g.components:
        seen: set[str] = set()
        cur: str | None = c.id
        while cur is not None:
            if cur in seen:
                errs.append(f"{c.id}: parent chain loops")
                break
            seen.add(cur)
            cur = parent.get(cur)
    return errs[:50]
