"""A shareable report, rendered only from stored engine results (no recomputation, no LLM)."""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime

from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectSpec
from app.schemas.results import OptimisationResult

TIER_TITLES = {
    "model": "Change the AI model",
    "software": "Change how you run it",
    "configure": "Change settings on the hardware you have",
    "select": "Choose different hardware or cooling",
    "design": "Redesign the chip or board (what-ifs)",
}
SOURCE_WORDS = {
    "measured": "measured on real hardware",
    "calibrated": "predicted, checked against real runs",
    "analytical": "predicted by the physics model",
    "vendor_spec": "taken from published specifications",
    "assumed": "an estimate",
    "extracted_unverified": "extracted from a datasheet, not yet confirmed",
}


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def build_report_md(
    spec: ProjectSpec,
    model: ModelProfile,
    hw: HardwareProfile,
    res: OptimisationResult,
    generated_at: datetime | None = None,
) -> str:
    v = res.verdict
    c = spec.constraints
    wl = spec.workload
    out: list[str] = []
    a = out.append
    a(f"# The Green Computer report: {model.name} on {hw.name}")
    a("")
    a(f"Project: {spec.name}")
    a("")
    if generated_at:
        a(f"_Generated {generated_at:%Y-%m-%d %H:%M} UTC from the stored analysis. Engine "
          f"{res.baseline.engine_version}._")
        a("")
    a("## 1. The question")
    a("")
    a(f"Run **{model.name}** on **{hw.name}** at **{wl.fps:.0f} frames per second** in a "
      f"**{wl.ambient_c:.0f} °C** environment, using at most **{c.max_power_w:.0f} W**, "
      f"keeping at least **{c.min_accuracy * 100:.0f}%** of the model's accuracy"
      + (f", responding within **{c.max_latency_ms:.0f} ms**" if c.max_latency_ms else "")
      + ", without overheating.")
    a("")
    a("## 2. The verdict")
    a("")
    if v:
        a(f"**{v.summary}**")
        a("")
        a("| | Current setup | Recommended setup |")
        a("|---|---|---|")
        i = v.improved
        rows = [
            ("Result", v.baseline.headline, i.headline if i else "n/a"),
            ("Average power", f"{v.baseline.power_w:.1f} W", f"{i.power_w:.1f} W" if i else "n/a"),
            ("Frame latency", f"{v.baseline.latency_ms:.0f} ms", f"{i.latency_ms:.0f} ms" if i else "n/a"),
            ("Temperature", f"{v.baseline.temperature_c:.0f} °C", f"{i.temperature_c:.0f} °C" if i else "n/a"),
            ("Speed", f"{v.baseline.capacity_fps:.0f} frames/s", f"{i.capacity_fps:.0f} frames/s" if i else "n/a"),
            ("Energy per frame", f"{v.baseline.energy_mj:.0f} mJ", f"{i.energy_mj:.0f} mJ" if i else "n/a"),
        ]
        for name, x, y in rows:
            a(f"| {name} | {_cell(x)} | {_cell(y)} |")
        if v.savings:
            s = v.savings
            a("")
            a(f"Savings: {s.power_percent:.0f}% less power, {s.energy_percent:.0f}% less energy per "
              f"frame, about {s.cost_per_year:.2f} less electricity cost (in your price units) and "
              f"{s.carbon_kg_per_year:.0f} kg less CO₂e per year at your grid.")
    a("")
    a("## 3. The cheapest fix")
    a("")
    if res.repair:
        a(res.repair.message)
        a("")
        for n, p in enumerate(res.repair.plans, 1):
            a(f"{n}. **{p.feasibility.value.replace('_', ' ').title()}**, effort {p.effort_score:.0f}: "
              + "; ".join(p.changes)
              + (f" (fixes: {', '.join(p.fixes)})" if p.fixes else ""))
            if p.note:
                a(f"   - {p.note}")
        if res.repair.binding_constraints:
            a("")
            a("Binding constraints: " + ", ".join(res.repair.binding_constraints))
    a("")
    a("## 4. Recommendations")
    a("")
    for tier, title in TIER_TITLES.items():
        items = [adv for adv in res.advice if adv.tier == tier]
        if not items:
            continue
        a(f"### {title}")
        a("")
        for adv in items:
            a(f"- **{adv.title}** ({adv.gain}; confidence {adv.confidence}; effort {adv.effort})")
            a(f"  - Do: {adv.action}")
            a(f"  - Why: {adv.why}")
            for t in adv.tradeoffs:
                a(f"  - Trade-off: {t}")
            a(f"  - How sure: {adv.confidence_reason}")
            if adv.validate_steps:
                a("  - Check it: " + "; ".join(adv.validate_steps))
        a("")
    a("## 5. Other models and hardware")
    a("")
    if res.alternatives:
        a("| Option | Result | Power | Latency | Energy saving | Accuracy vs current |")
        a("|---|---|---|---|---|---|")
        for alt in res.alternatives:
            acc = f"{alt.accuracy_retention * 100:.0f}%" if alt.accuracy_retention else "n/a"
            sav = f"{alt.energy_saving_percent:.0f}% (vs {alt.compared_with})" if alt.energy_saving_percent is not None else "n/a"
            a(f"| {_cell(alt.name)} | {alt.feasibility.value.replace('_', ' ')} | {alt.power_w:.1f} W | "
              f"{alt.latency_ms:.0f} ms | {sav} | {acc} |")
    else:
        a("No alternatives were evaluated.")
    a("")
    a("## 6. What limits this workload, and chip-design what-ifs")
    a("")
    a(res.limiting_factor or "Not determined.")
    a("")
    if res.whatifs:
        a("| Change | Energy | Latency | Power | Temperature |")
        a("|---|---|---|---|---|")
        for w in res.whatifs:
            a(f"| {_cell(w.change)} | {w.energy_change_percent:+.0f}% | "
              f"{w.latency_change_percent:+.0f}% | {w.power_change_percent:+.0f}% | "
              f"{w.temperature_change_c:+.1f} °C |")
        a("")
        a("These are analytical what-ifs on one parameter at a time. Proving any of them needs "
          "architecture-level modelling, synthesis and silicon.")
    a("")
    a("## 7. How sure are we?")
    a("")
    best = res.best or res.baseline
    counts = Counter(m.provenance.value for m in best.metrics.values())
    for src, n in counts.most_common():
        a(f"- {n} figure(s) {SOURCE_WORDS.get(src, src)}")
    a("")
    a("Every prediction carries a best-case to worst-case range; these are bounds, not statistical "
      "intervals. Anything marked 'uncertain' needs a real measurement (see the Verify step). "
      "Your requirements were never relaxed to find a result.")
    a("")
    a("## 8. Assumptions and limits")
    a("")
    for line in (
        "The performance model is a per-layer roofline with a coupled power, temperature and "
        "throttling solve; its constants are assumptions until calibrated on your device.",
        "Accuracy effects of INT8, pruning and smaller inputs are estimates until you measure them "
        "on your own validation data.",
        "Hardware design what-ifs ignore on-chip memory hierarchy and dataflow.",
        "Nothing here was executed on a device unless a figure is labelled measured.",
    ):
        a(f"- {line}")
    a("")
    return "\n".join(out)


def _inline(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"_(.+?)_", r"<em>\1</em>", t)


def md_to_html(md: str, title: str = "The Green Computer report") -> str:
    """Tiny Markdown subset (headings, tables, nested bullets, numbered lists, bold) to HTML."""
    out: list[str] = []
    lines = md.split("\n")
    i = 0
    in_ul = in_ol = False

    def close() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[-| ]+\|$", lines[i + 1]):
            close()
            head = [c.strip() for c in ln.strip("|").split("|")]
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,3}) (.*)", ln)
        if m:
            close()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif re.match(r"^\s*- ", ln):
            if not in_ul:
                close()
                out.append("<ul>")
                in_ul = True
            indent = len(ln) - len(ln.lstrip())
            out.append(f"<li style='margin-left:{indent * 6}px'>{_inline(ln.strip()[2:])}</li>")
        elif re.match(r"^\d+\. ", ln):
            if not in_ol:
                close()
                out.append("<ol>")
                in_ol = True
            item = re.sub(r"^\d+\. ", "", ln)
            out.append(f"<li>{_inline(item)}</li>")
        elif ln.strip():
            close()
            out.append(f"<p>{_inline(ln)}</p>")
        else:
            close()
        i += 1
    close()
    style = (
        "body{font:15px/1.55 system-ui,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;color:#17231b}"
        "table{border-collapse:collapse;width:100%;margin:10px 0}th,td{border:1px solid #dde5df;padding:6px 8px;text-align:left}"
        "th{background:#f0f5f1}h1{font-size:24px}h2{margin-top:28px;border-bottom:1px solid #dde5df;padding-bottom:4px}"
    )
    return (
        f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
        f"<style>{style}</style><body>{''.join(out)}</body></html>"
    )
