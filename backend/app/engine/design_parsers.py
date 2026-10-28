"""Bounded readers for hardware design files: what they state, never what they would do.

  kicad_sch     KiCad 6+ schematic (.kicad_sch)  -> title block, parts (reference, value, footprint,
                library symbol), hierarchical sheets. Wire connectivity is not traced.
  kicad_pcb     KiCad 6+ / 5 board (.kicad_pcb)   -> board outline size, copper layers, thickness,
                footprints with placement (x, y, rotation, side) and the nets on their pads.
  verilog       Verilog-2001 / a SystemVerilog subset (.v, .sv) -> modules, parameters, ports with
                widths, instances. No elaboration, no simulation, no synthesis.
  vivado_timing Vivado report_timing_summary -> WNS/TNS/WHS/THS, failing endpoints, clocks.
  hls_report    Vitis/Vivado HLS csynth.rpt or csynth.xml -> latency, interval, clock, resources.

Every parser is pure (text in, dict out), bounded in size and nesting, and says what it skipped.
Nothing here is an electrical, thermal, timing or functional simulation: timing numbers come only
from Vivado's own report, and are labelled as such by the caller.
"""

from __future__ import annotations

import re
from typing import Any

MAX_TEXT = 20 * 1024 * 1024
MAX_DEPTH = 200
MAX_PARTS = 2000
MAX_MODULES = 500
MAX_NET_CONNECTIONS = 4000


class DesignParseError(ValueError):
    pass


# --- S-expressions (KiCad) -------------------------------------------------------------------

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
SExpr = list[Any]


def parse_sexpr(text: str) -> SExpr:
    """Parse one S-expression into nested lists of strings. Iterative, depth-limited."""
    if len(text) > MAX_TEXT:
        raise DesignParseError("file too large to read")
    stack: list[SExpr] = []
    root: SExpr | None = None
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "(":
            node: SExpr = []
            if stack:
                stack[-1].append(node)
            elif root is not None:
                break  # trailing content after the first expression is ignored
            else:
                root = node
            stack.append(node)
            if len(stack) > MAX_DEPTH:
                raise DesignParseError("nesting is deeper than any KiCad file should be")
        elif tok == ")":
            if not stack:
                raise DesignParseError("unbalanced parentheses")
            stack.pop()
            if not stack:
                break
        elif stack:
            stack[-1].append(tok[1:-1].replace('\\"', '"') if tok.startswith('"') else tok)
    if root is None:
        raise DesignParseError("no S-expression found")
    if stack:
        raise DesignParseError("the file ends before its last parenthesis closes (truncated?)")
    return root


def _head(n: Any) -> str | None:
    return n[0] if isinstance(n, list) and n and isinstance(n[0], str) else None


def _kids(n: SExpr, name: str) -> list[SExpr]:
    return [c for c in n[1:] if _head(c) == name]


def _kid(n: SExpr, name: str) -> SExpr | None:
    for c in n[1:]:
        if _head(c) == name:
            return c
    return None


def _val(n: SExpr | None, i: int = 1) -> str | None:
    if n is None or len(n) <= i or not isinstance(n[i], str):
        return None
    return n[i]


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def _props(n: SExpr) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in _kids(n, "property"):
        if len(p) >= 3 and isinstance(p[1], str) and isinstance(p[2], str):
            out[p[1]] = p[2]
    return out


_REF_KIND = [
    ("U", "ic"), ("IC", "ic"), ("Q", "discrete"), ("D", "discrete"), ("LED", "discrete"),
    ("R", "passive"), ("C", "passive"), ("L", "passive"), ("FB", "passive"), ("F", "passive"),
    ("J", "connector"), ("P", "connector"), ("CN", "connector"), ("Y", "clock"), ("X", "clock"),
    ("SW", "other"), ("TP", "other"), ("H", "other"), ("MH", "other"),
]  # fmt: skip


def part_kind(ref: str) -> str:
    prefix = re.match(r"[A-Za-z]+", ref)
    p = prefix.group(0).upper() if prefix else ""
    for pre, kind in sorted(_REF_KIND, key=lambda x: -len(x[0])):
        if p == pre:
            return kind
    return "other"


def parse_kicad_sch(text: str) -> dict[str, Any]:
    if text.lstrip().startswith("EESchema"):
        raise DesignParseError("This is a KiCad 5 legacy schematic (.sch). Open it in KiCad 6 or later "
                               "and save it as .kicad_sch, then upload that.")  # fmt: skip
    root = parse_sexpr(text)
    if _head(root) != "kicad_sch":
        raise DesignParseError("not a KiCad schematic (expected a (kicad_sch ...) file)")
    tb = _kid(root, "title_block")
    title = {k: _val(_kid(tb, k)) for k in ("title", "date", "rev", "company")} if tb else {}
    parts: list[dict[str, Any]] = []
    power_symbols = 0
    skipped = 0
    for s in _kids(root, "symbol"):  # top-level placed symbols (lib_symbols holds definitions)
        props = _props(s)
        ref = props.get("Reference", "")
        lib_id = _val(_kid(s, "lib_id")) or ""
        if not ref or ref.startswith("#") or lib_id.startswith("power:"):
            power_symbols += 1
            continue
        if len(parts) >= MAX_PARTS:
            skipped += 1
            continue
        at = _kid(s, "at")
        parts.append({"ref": ref, "value": props.get("Value", ""), "footprint": props.get("Footprint", ""),
                      "lib_id": lib_id, "datasheet": props.get("Datasheet", "") if props.get("Datasheet", "~") != "~" else "",
                      "kind": part_kind(ref), "sheet_x": _num(_val(at, 1)), "sheet_y": _num(_val(at, 2))})  # fmt: skip
    sheets = [{"name": _props(sh).get("Sheetname") or _props(sh).get("Sheet name", ""),
               "file": _props(sh).get("Sheetfile") or _props(sh).get("Sheet file", "")}
              for sh in _kids(root, "sheet")]  # fmt: skip
    labels = sorted({_val(lb) or "" for lb in _kids(root, "global_label") + _kids(root, "hierarchical_label")} - {""})
    if not parts and not sheets:
        raise DesignParseError("the schematic has no placed parts or sheets")
    warnings = []
    if sheets:
        warnings.append(f"{len(sheets)} sub-sheet(s) are referenced; upload each .kicad_sch to include their parts.")
    if skipped:
        warnings.append(f"Only the first {MAX_PARTS} parts were read; {skipped} more were skipped.")
    warnings.append("Wire connectivity is not traced from the schematic; upload the .kicad_pcb for nets.")
    return {"tool": "kicad_sch", "version": _val(_kid(root, "version")), "generator": _val(_kid(root, "generator")),
            "title": title, "parts": parts, "sheets": sheets, "labels": labels[:200],
            "power_symbols": power_symbols, "warnings": warnings,
            "note": "Metadata read from the schematic file. Nothing was simulated."}  # fmt: skip


def parse_kicad_pcb(text: str) -> dict[str, Any]:
    root = parse_sexpr(text)
    if _head(root) != "kicad_pcb":
        raise DesignParseError("not a KiCad board (expected a (kicad_pcb ...) file)")
    general = _kid(root, "general")
    thickness = _num(_val(_kid(general, "thickness"))) if general else None
    layers = _kid(root, "layers")
    copper = [x[1] for x in (layers[1:] if layers else []) if isinstance(x, list) and len(x) > 1
              and isinstance(x[1], str) and x[1].endswith(".Cu")]  # fmt: skip
    nets = {(_val(n) or ""): (_val(n, 2) or "") for n in _kids(root, "net")}

    xs: list[float] = []
    ys: list[float] = []
    for g in _kids(root, "gr_line") + _kids(root, "gr_rect") + _kids(root, "gr_arc") + _kids(root, "gr_poly"):
        if _val(_kid(g, "layer")) != "Edge.Cuts":
            continue
        for key in ("start", "end", "mid"):
            p = _kid(g, key)
            if p is not None and _num(_val(p, 1)) is not None and _num(_val(p, 2)) is not None:
                xs.append(float(p[1]))
                ys.append(float(p[2]))
        pts = _kid(g, "pts")
        for xy in _kids(pts, "xy") if pts else []:
            if _num(_val(xy, 1)) is not None and _num(_val(xy, 2)) is not None:
                xs.append(float(xy[1]))
                ys.append(float(xy[2]))
    outline = ({"x_mm": min(xs), "y_mm": min(ys), "width_mm": round(max(xs) - min(xs), 3),
                "height_mm": round(max(ys) - min(ys), 3)} if xs and ys else None)  # fmt: skip

    parts: list[dict[str, Any]] = []
    net_parts: dict[str, set[str]] = {}
    skipped = 0
    for fp in _kids(root, "footprint") + _kids(root, "module"):
        props = _props(fp)
        ref = props.get("Reference") or next((_val(t, 2) for t in _kids(fp, "fp_text") if _val(t) == "reference"), "") or ""
        value = props.get("Value") or next((_val(t, 2) for t in _kids(fp, "fp_text") if _val(t) == "value"), "") or ""
        if not ref or ref.startswith("#"):
            continue
        if len(parts) >= MAX_PARTS:
            skipped += 1
            continue
        at = _kid(fp, "at")
        side = "bottom" if (_val(_kid(fp, "layer")) or "").startswith("B.") else "top"
        pad_nets = sorted({_val(_kid(p, "net"), 2) or "" for p in _kids(fp, "pad")} - {""})
        for n in pad_nets:
            net_parts.setdefault(n, set()).add(ref)
        parts.append({"ref": ref, "value": value, "footprint": _val(fp) or "", "kind": part_kind(ref),
                      "x_mm": _num(_val(at, 1)), "y_mm": _num(_val(at, 2)), "rotation": _num(_val(at, 3)) or 0.0,
                      "side": side, "nets": pad_nets[:64]})  # fmt: skip
    if not parts:
        raise DesignParseError("the board has no footprints")
    # Small nets become point-to-point connections; wide nets (ground, supplies) are summarised.
    wide = sorted((n for n, ps in net_parts.items() if len(ps) > 6), key=lambda n: -len(net_parts[n]))
    connections: list[dict[str, Any]] = []
    for n, ps in sorted(net_parts.items()):
        if 2 <= len(ps) <= 6:
            refs = sorted(ps)
            for other in refs[1:]:
                if len(connections) >= MAX_NET_CONNECTIONS:
                    break
                connections.append({"net": n, "a": refs[0], "b": other})
    warnings = ["Copper geometry, impedance, current capacity and thermal spreading are not analysed."]
    if skipped:
        warnings.append(f"Only the first {MAX_PARTS} footprints were read; {skipped} more were skipped.")
    if outline is None:
        warnings.append("No board outline on Edge.Cuts, so the board size is unknown.")
    return {"tool": "kicad_pcb", "version": _val(_kid(root, "version")), "thickness_mm": thickness,
            "copper_layers": copper, "net_count": len([n for n in nets.values() if n]), "outline": outline,
            "parts": parts, "connections": connections,
            "wide_nets": [{"net": n, "parts": len(net_parts[n])} for n in wide[:40]],
            "warnings": warnings, "note": "Metadata read from the board file. Nothing was simulated."}  # fmt: skip


# --- Verilog subset --------------------------------------------------------------------------

_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic", "assign", "always",
    "always_ff", "always_comb", "always_latch", "initial", "begin", "end", "if", "else", "case",
    "endcase", "for", "generate", "endgenerate", "genvar", "integer", "parameter", "localparam",
    "function", "endfunction", "task", "endtask", "return", "posedge", "negedge", "or", "and", "not",
    "typedef", "struct", "enum", "default", "while", "repeat", "forever", "signed", "unsigned", "bit",
    "byte", "int", "real", "time", "string", "import", "export", "package", "endpackage", "interface",
    "endinterface", "modport", "casez", "casex", "unique", "priority", "automatic", "static", "const",
    "supply0", "supply1", "tri", "wand", "wor", "specify", "endspecify", "defparam", "buf", "assert",
}  # fmt: skip


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)


def _width(rng: str | None, params: dict[str, str]) -> int | str:
    if not rng:
        return 1
    m = re.fullmatch(r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]", rng.strip())
    if not m:
        return rng.strip()
    hi, lo = m.group(1), m.group(2)

    def ev(s: str) -> int | None:
        s = s.strip()
        for k, v in params.items():
            s = re.sub(rf"\b{re.escape(k)}\b", v, s)
        return _arith(s) if re.fullmatch(r"[\d\s+\-*/()]+", s) and len(s) < 80 else None

    h, l_ = ev(hi), ev(lo)
    return abs(h - l_) + 1 if h is not None and l_ is not None else f"[{hi}:{lo}]"


def _arith(expr: str) -> int | None:
    """Integer + - * / of literals only (no eval, no powers), for widths like [WIDTH-1:0]."""
    import ast

    def ev(n: ast.AST) -> int:
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return a // b
        raise ValueError("unsupported")

    try:
        return ev(ast.parse(expr, mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None


_PORT_DECL = re.compile(
    r"\b(input|output|inout)\b\s*(?:wire|reg|logic|var|tri)?\s*(?:signed|unsigned)?\s*(\[[^\]]*\])?\s*"
    r"((?:(?!\b(?:input|output|inout)\b)[^;()])*)"  # names up to the next direction keyword
)


def _ports_from(chunk: str, params: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # Split ANSI headers at each direction keyword so "input a, b, output c" works.
    for m in _PORT_DECL.finditer(chunk):
        names = re.split(r"\s*,\s*", m.group(3).strip())
        for raw in names:
            nm = re.match(r"(\w+)", raw.strip())
            if not nm or nm.group(1) in _KEYWORDS or nm.group(1) in ("input", "output", "inout"):
                continue
            out.append({"name": nm.group(1), "direction": m.group(1), "width": _width(m.group(2), params)})
    return out


def parse_verilog(text: str) -> dict[str, Any]:
    if len(text) > MAX_TEXT:
        raise DesignParseError("file too large to read")
    src = _strip_comments(text)
    if re.search(r"\bentity\b.+\bis\b", src, re.I | re.S) and "module" not in src:
        raise DesignParseError("This looks like VHDL. VHDL is not read yet; upload the Vivado "
                               "utilisation, power and timing reports instead.")  # fmt: skip
    modules: list[dict[str, Any]] = []
    warnings: list[str] = []
    for m in re.finditer(r"\bmodule\s+(\w+)(.*?)\bendmodule\b", src, re.S):
        if len(modules) >= MAX_MODULES:
            warnings.append(f"Only the first {MAX_MODULES} modules were read.")
            break
        name, body = m.group(1), m.group(2)
        header_end = body.find(";")
        header, inner = (body[:header_end], body[header_end + 1 :]) if header_end >= 0 else (body, "")
        params: dict[str, str] = {}
        for pm in re.finditer(r"\b(?:parameter|localparam)\b\s*(?:\w+\s+)?(?:\[[^\]]*\]\s*)?(\w+)\s*=\s*([^,;)]+)", body):
            params.setdefault(pm.group(1), pm.group(2).strip())
        ports = _ports_from(header, params)
        if not ports:  # non-ANSI: names in the header, directions in the body
            ports = _ports_from(";".join(s for s in inner.split(";") if re.match(r"\s*(input|output|inout)\b", s)), params)
        instances: list[dict[str, Any]] = []
        for st in re.split(r";|\bbegin\b|\bend\b|\bgenerate\b|\bendgenerate\b", inner):
            im = re.match(r"\s*(\w+)\s*(#\s*\((?:[^()]|\([^()]*\))*\))?\s*(\w+)\s*(\[[^\]]*\])?\s*\(", st)
            if not im or im.group(1) in _KEYWORDS or im.group(3) in _KEYWORDS:
                continue
            instances.append({"module": im.group(1), "name": im.group(3)})
        clocks = [p["name"] for p in ports if p["direction"] == "input" and re.search(r"cl(oc)?k", p["name"], re.I)]
        modules.append({"name": name, "parameters": params, "ports": ports[:256], "port_count": len(ports),
                        "instances": instances[:512], "clocks": clocks,
                        "line": text.count("\n", 0, m.start()) + 1})  # fmt: skip
    if not modules:
        raise DesignParseError("no Verilog module found (expected 'module name (...); ... endmodule')")
    defined = {md["name"] for md in modules}
    used = {i["module"] for md in modules for i in md["instances"]}
    external = sorted(used - defined)
    tops = sorted(defined - used)
    if re.search(r"\b(interface|package|class|program)\b\s+\w+", src):
        warnings.append("SystemVerilog interfaces, packages or classes are present; they are not read.")
    if external:
        warnings.append(f"{len(external)} instantiated module(s) are not in this file (IP, primitives or other files): "
                        + ", ".join(external[:12]) + ("…" if len(external) > 12 else ""))  # fmt: skip
    warnings.append("Structure only: the design was not elaborated, simulated or synthesised.")
    return {"tool": "verilog", "modules": modules, "top_modules": tops, "external_modules": external,
            "warnings": warnings, "note": "Module structure read from the source text (a Verilog subset)."}  # fmt: skip


# --- Vivado timing summary --------------------------------------------------------------------


def parse_vivado_timing(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    summary: dict[str, float | int] = {}
    for i, ln in enumerate(lines):
        if "WNS(ns)" in ln and "TNS(ns)" in ln:
            heads = re.split(r"\s{2,}", ln.strip())  # headers are multi-word, columns 2+ spaces apart
            for row in lines[i + 1 : i + 4]:
                vals = row.split()
                if vals and not set(row.strip()) <= {"-", " "} and len(vals) == len(heads):
                    for h, v in zip(heads, vals, strict=True):
                        n = _num(v)
                        if n is not None:
                            key = h.lower().replace("(ns)", "_ns").replace(" ", "_").strip("_")
                            summary[key] = int(n) if "endpoints" in key else n
                    break
            break
    if "wns_ns" not in summary:
        raise DesignParseError("no Design Timing Summary table (expected report_timing_summary output)")
    clocks: list[dict[str, Any]] = []
    for m in re.finditer(r"^\s*(\S+)\s+\{([\d.\s]+)\}\s+([\d.]+)\s+([\d.]+)\s*$", text, re.M):
        clocks.append({"name": m.group(1), "period_ns": float(m.group(3)), "frequency_mhz": float(m.group(4))})
    met = "All user specified timing constraints are met" in text
    failing = summary.get("tns_failing_endpoints", 0) or 0
    return {"tool": "vivado_timing", "summary": summary, "clocks": clocks[:64],
            "constraints_met": bool(met and float(summary.get("wns_ns", 0)) >= 0 and not failing),
            "note": "Timing as analysed by Vivado (a real static timing analysis by that tool)."}  # fmt: skip


# --- HLS synthesis report ---------------------------------------------------------------------


def _xml_tag(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", text)
    return m.group(1) if m else None


def parse_hls(text: str) -> dict[str, Any]:
    if "<profile>" in text or "<AreaEstimates>" in text:
        top = _xml_tag(text, "TopModelName")
        res = {k: _num(_xml_tag(text, k)) for k in ("BRAM_18K", "DSP", "DSP48E", "FF", "LUT", "URAM")}
        out: dict[str, Any] = {
            "tool": "hls_report", "format": "csynth.xml", "top": top, "part": _xml_tag(text, "Part"),
            "target_clock_ns": _num(_xml_tag(text, "TargetClockPeriod")),
            "estimated_clock_ns": _num(_xml_tag(text, "EstimatedClockPeriod")),
            "latency_cycles": {"best": _num(_xml_tag(text, "Best-caseLatency")), "worst": _num(_xml_tag(text, "Worst-caseLatency"))},
            "interval_cycles": {"min": _num(_xml_tag(text, "Interval-min")), "max": _num(_xml_tag(text, "Interval-max"))},
            "resources": {k: v for k, v in res.items() if v is not None},
        }  # fmt: skip
    else:
        top_m = re.search(r"==\s*(?:Vitis|Vivado)\s+HLS Report for '([^']+)'", text)
        if not top_m:
            raise DesignParseError("not an HLS synthesis report (expected \"== Vitis HLS Report for 'name'\")")
        clk = re.search(r"\|\s*ap_clk\s*\|\s*([\d.]+)\s*ns\s*\|\s*([\d.]+)\s*ns", text)
        lat = None
        li = text.find("Latency (cycles)")
        if li >= 0:
            for row in text[li:].splitlines()[1:8]:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if len(cells) >= 6 and _num(cells[0]) is not None and _num(cells[1]) is not None:
                    lat = cells
                    break
        res_r: dict[str, float] = {}
        avail: dict[str, float] = {}
        hm = re.search(r"\|\s*Name\s*\|(.*BRAM_18K.*)\|\s*$", text, re.M)
        if hm:
            cols = [c.strip() for c in hm.group(1).split("|")]
            for label, target in (("Total", res_r), ("Available", avail)):
                rm = re.search(rf"^\|\s*{label}\s*\|(.*)\|\s*$", text[hm.end():], re.M)
                if rm:
                    for c, v in zip(cols, [x.strip() for x in rm.group(1).split("|")], strict=False):
                        n = _num(v)
                        if c and n is not None:
                            target[c] = n
        out = {
            "tool": "hls_report", "format": "csynth.rpt", "top": top_m.group(1),
            "part": (m.group(1).strip() if (m := re.search(r"\*\s*Target device:\s*(\S+)", text)) else None),
            "target_clock_ns": _num(clk.group(1)) if clk else None,
            "estimated_clock_ns": _num(clk.group(2)) if clk else None,
            "latency_cycles": {"best": _num(lat[0]), "worst": _num(lat[1])} if lat else {"best": None, "worst": None},
            "interval_cycles": {"min": _num(lat[4]), "max": _num(lat[5])} if lat else {"min": None, "max": None},
            "resources": res_r, "available": avail,
        }  # fmt: skip
    if out["latency_cycles"]["best"] is None and not out["resources"]:
        raise DesignParseError("the HLS report has neither latency nor resource figures")
    t = out.get("target_clock_ns") or out.get("estimated_clock_ns")  # as the HLS tool's absolute latency
    if t and out["latency_cycles"]["worst"] is not None:
        out["latency_us_worst"] = round(out["latency_cycles"]["worst"] * t / 1000, 4)
    out["note"] = ("HLS estimates before place and route: real clock and resources are known only "
                   "after implementation (upload the Vivado reports).")  # fmt: skip
    return out


def detect_design_report(text: str) -> str | None:
    t = text[:200_000]
    if "WNS(ns)" in t and "TNS(ns)" in t:
        return "vivado_timing"
    if re.search(r"==\s*(?:Vitis|Vivado)\s+HLS Report for", t) or ("<profile>" in t and "<PerformanceEstimates>" in t):
        return "hls_report"
    return None
