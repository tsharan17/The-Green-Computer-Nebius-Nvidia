"""Bounded design-file readers and the system graph builder (pure)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.data_loader import get_device
from app.engine import design_parsers as d
from app.engine import system_graph as sg

F = Path(__file__).resolve().parent.parent / "fixtures" / "design"


def text(name: str) -> str:
    return (F / name).read_text(encoding="utf-8")


def test_kicad_schematic_parts_title_and_sheets():
    r = d.parse_kicad_sch(text("camera.kicad_sch"))
    refs = {p["ref"]: p for p in r["parts"]}
    assert set(refs) == {"U1", "U2", "R1", "C1", "J1"}  # the power symbol and library definitions are skipped
    assert refs["U2"]["value"] == "TPS54560" and refs["U2"]["kind"] == "ic" and refs["R1"]["kind"] == "passive"
    assert refs["U2"]["datasheet"] == ""  # "~" means none
    assert r["title"]["rev"] == "B" and r["sheets"] == [{"name": "Power", "file": "power.kicad_sch"}]
    assert "MIPI_CLK_P" in r["labels"] and r["power_symbols"] == 1
    assert any("not traced" in w for w in r["warnings"])


def test_kicad_board_outline_layers_placement_and_nets():
    r = d.parse_kicad_pcb(text("camera.kicad_pcb"))
    assert r["outline"] == {"x_mm": 100.0, "y_mm": 50.0, "width_mm": 70.0, "height_mm": 50.0}
    assert len(r["copper_layers"]) == 4 and r["thickness_mm"] == 1.6 and r["net_count"] == 4
    parts = {p["ref"]: p for p in r["parts"]}
    assert parts["R1"]["side"] == "bottom" and parts["U2"]["rotation"] == 90.0 and parts["U1"]["x_mm"] == 130.0
    assert {"net": "FB", "a": "R1", "b": "U2"} in r["connections"]


def test_verilog_modules_ports_widths_instances():
    r = d.parse_verilog(text("accel.v"))
    mods = {m["name"]: m for m in r["modules"]}
    assert r["top_modules"] == ["conv_top"] and r["external_modules"] == ["xpm_fifo_sync"]
    top = mods["conv_top"]
    widths = {p["name"]: p["width"] for p in top["ports"]}
    assert widths == {"clk": 1, "rst_n": 1, "s_axis_tdata": 128, "s_axis_tvalid": 1, "s_axis_tready": 1,
                      "m_axis_tdata": 32, "m_axis_tvalid": 1}  # fmt: skip
    assert {i["name"] for i in top["instances"]} == {"u_buf", "u_mac", "u_fifo"}
    assert mods["mac_array"]["port_count"] == 3  # non-ANSI port declarations
    assert top["clocks"] == ["clk"] and top["parameters"] == {"DATA_W": "8", "LANES": "16"}


def test_verilog_width_arithmetic_is_safe():
    # Powers and names never reach an evaluator; a symbolic range stays as text.
    assert d._width("[9**9**9:0]", {}) == "[9**9**9:0]"
    assert d._width("[N-1:0]", {}) == "[N-1:0]"
    assert d._width("[W*2-1:0]", {"W": "4"}) == 8
    assert d._arith("__import__('os')") is None


def test_vivado_timing_and_hls_reports():
    t = d.parse_vivado_timing(text("timing.rpt"))
    assert t["summary"]["wns_ns"] == 0.412 and t["summary"]["tns_failing_endpoints"] == 0 and t["constraints_met"]
    assert t["clocks"] == [{"name": "clk_pl_0", "period_ns": 10.0, "frequency_mhz": 100.0}]
    h = d.parse_hls(text("conv_top_csynth.rpt"))
    assert h["top"] == "conv_top" and h["latency_cycles"] == {"best": 51234.0, "worst": 52010.0}
    assert h["resources"]["DSP"] == 16 and h["available"]["LUT"] == 117120 and h["latency_us_worst"] == 520.1
    xml = ("<profile><UserAssignments><TopModelName>k</TopModelName><TargetClockPeriod>5</TargetClockPeriod>"
           "</UserAssignments><PerformanceEstimates><SummaryOfOverallLatency><Best-caseLatency>100</Best-caseLatency>"
           "<Worst-caseLatency>120</Worst-caseLatency></SummaryOfOverallLatency></PerformanceEstimates>"
           "<AreaEstimates><Resources><DSP>4</DSP><LUT>900</LUT></Resources></AreaEstimates></profile>")  # fmt: skip
    hx = d.parse_hls(xml)
    assert hx["format"] == "csynth.xml" and hx["latency_us_worst"] == 0.6 and hx["resources"] == {"DSP": 4.0, "LUT": 900.0}
    assert d.detect_design_report(text("timing.rpt")) == "vivado_timing"
    assert d.detect_design_report(text("conv_top_csynth.rpt")) == "hls_report"


@pytest.mark.parametrize("fn,bad,msg", [
    (d.parse_kicad_sch, "EESchema Schematic File Version 4\n", "KiCad 5"),
    (d.parse_kicad_sch, "(kicad_pcb (version 1))", "not a KiCad schematic"),
    (d.parse_kicad_sch, "(kicad_sch (version 1) (symbol (lib_id \"x\")", "truncated"),
    (d.parse_kicad_pcb, "(kicad_pcb (version 1))", "no footprints"),
    (d.parse_kicad_pcb, "(" * 300 + ")" * 300, "nesting"),
    (d.parse_verilog, "entity foo is end foo;", "VHDL"),
    (d.parse_verilog, "just some text", "no Verilog module"),
    (d.parse_vivado_timing, "nothing here", "Design Timing Summary"),
    (d.parse_hls, "no report", "HLS"),
])  # fmt: skip
def test_bad_design_files_are_refused_with_a_reason(fn, bad, msg):
    with pytest.raises(d.DesignParseError, match=msg):
        fn(bad)


def test_graph_base_links_trace_components_and_merges_files():
    hw = get_device("xilinx-kria-kv260")
    g = sg.base_graph(hw)
    ids = {c.id for c in g.components}
    assert {"system", "compute", "ram", "power", "cooling"} <= ids
    assert {c.trace_component for c in g.components if c.trace_component} >= {"compute", "ram", "power", "cooling"}
    assert sg.validate(g) == []

    g2 = sg.merge_parsed(g, d.parse_verilog(text("accel.v")), 7, "accel.v")
    g2 = sg.merge_parsed(g2, d.parse_vivado_timing(text("timing.rpt")), 8, "timing.rpt")
    g2 = sg.merge_parsed(g2, d.parse_hls(text("conv_top_csynth.rpt")), 9, "csynth.rpt")
    g2 = sg.merge_parsed(g2, d.parse_kicad_sch(text("camera.kicad_sch")), 10, "camera.kicad_sch")
    g2 = sg.merge_parsed(g2, d.parse_kicad_pcb(text("camera.kicad_pcb")), 11, "camera.kicad_pcb")
    assert sg.validate(g2) == []
    by = {c.id: c for c in g2.components}
    assert by["rtl:conv_top"].parent == "compute" and by["rtl:xpm_fifo_sync"].confidence == "assumed"
    assert any(e.kind == "instance" and e.target == "rtl:mac_array" for e in g2.connections)
    comp = by["compute"].attributes
    assert comp["timing_wns_ns"].value == 0.412 and comp["timing_wns_ns"].artifact_id == 8 and comp["timing_met"].value is True
    assert by["clock:clk_pl_0"].attributes["frequency"].value == 100.0
    # The HLS top merges into the Verilog module of the same name, keeping both origins.
    assert by["rtl:conv_top"].attributes["latency_worst"].value == 52010.0
    # Schematic and board describe the same part: one component, with physical placement from the board.
    u2 = by["part:U2"]
    assert u2.physical is not None and u2.physical.rotation_deg == 90.0 and by["part:R1"].physical.side == "bottom"
    assert by["board"].attributes["width"].value == 70.0
    assert {s.artifact_id for s in g2.sources} >= {7, 8, 9, 10, 11}
    assert g2.limitations and any("simulation" in x for x in g2.limitations)
    # Original profile facts are never overwritten.
    assert by["compute"].attributes["dynamic_power_max"].origin.startswith("library:")

    delta = sg.diff(g, g2)
    assert "rtl:conv_top" in delta["components_added"] and not delta["components_removed"]
    assert any(c["attribute"] == "timing_wns_ns" for c in delta["attributes_changed"])


def test_graph_validation_catches_structure_problems():
    g = sg.base_graph(get_device("jetson-orin-nano"))
    bad = g.model_copy(deep=True)
    bad.components[1].parent = "nope"
    bad.components.append(bad.components[2].model_copy())
    bad.connections[0].target = "ghost"
    errs = sg.validate(bad)
    assert any("parent nope" in e for e in errs) and any("duplicate" in e for e in errs) and any("both ends" in e for e in errs)
