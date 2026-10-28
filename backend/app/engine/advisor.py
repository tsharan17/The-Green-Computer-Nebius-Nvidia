"""Turn engine results into plain-language recommendations across four levels:

  model      change the AI model (quantise, prune, lower resolution, pick a lighter model)
  software   change how it runs (batching, runtime)
  configure  change settings on the hardware you own (power mode, clock)
  select     choose different hardware or cooling
  design     redesign the chip/board (what-ifs; analytical only)

Every number is an engine output. "Contribution" is measured by re-running the recommended
configuration with that one change undone, so each recommendation shows what it adds to the
combined plan, not just what it does in isolation.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine.changes import (
    EFFORT_BATCH,
    EFFORT_CLOCK,
    EFFORT_COOLING,
    EFFORT_MODEL_SWAP,
    EFFORT_NEW_DEVICE,
    EFFORT_POWER_MODE,
    PRECISION_EFFORT,
)
from app.schemas.advice import Advice, Alternative, WhatIf
from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.metrics import Feasibility, Provenance
from app.schemas.results import EvaluationResult

CERTIFIED = (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)
TIER_ORDER = {"model": 0, "software": 1, "configure": 2, "select": 3, "design": 4}

Ev = Callable[[Config], EvaluationResult]


def _effort_label(score: float) -> str:
    return "low" if score <= 1.5 else "medium" if score <= 3.5 else "high"


def _pct(old: float, new: float) -> float:
    return (1.0 - new / old) * 100.0 if old else 0.0


def _confidence(r: EvaluationResult, accuracy_prior: bool) -> tuple[str, str]:
    prov = r.metrics["latency"].provenance
    if accuracy_prior:
        return "low", "its accuracy effect is an unmeasured estimate"
    if prov == Provenance.MEASURED:
        return "high", "backed by a real measurement of this exact setup"
    if prov == Provenance.CALIBRATED:
        return "high", "predictions for this device were checked against real runs"
    return "medium", "a physics prediction not yet checked against real measurements"


def _gain(e_saving: float, p_saving: float, dtemp: float, throttled_before: bool) -> str:
    """Headline number for one change. Positive savings read as a reduction."""
    if throttled_before and dtemp < -5:
        return f"−{abs(dtemp):.0f} °C, stops slowing down"
    if abs(e_saving) >= 2:
        return f"−{e_saving:.0f}% energy" if e_saving > 0 else f"+{-e_saving:.0f}% energy"
    if abs(p_saving) >= 2:
        return f"−{p_saving:.0f}% power" if p_saving > 0 else f"+{-p_saving:.0f}% power"
    return "small effect alone"


def knob_advice(
    ev: Ev,
    hw: HardwareProfile,
    base: EvaluationResult,
    best: EvaluationResult,
) -> list[Advice]:
    bc, nc = base.config, best.config
    out: list[Advice] = []
    bm, nm = base.metrics, best.metrics

    def without(**kw) -> EvaluationResult:
        return ev(nc.model_copy(update=kw))

    def alone(**kw) -> EvaluationResult:
        return ev(bc.model_copy(update=kw))

    def fixes_of(r: EvaluationResult) -> list[str]:
        st = {c.name: c.status for c in r.checks}
        return [c.name for c in base.checks if c.status != "pass" and st.get(c.name) == "pass"]

    def add(
        key: str, tier: str, title: str, action: str, why: str, effort_score: float,
        tradeoffs: list[str], validate: list[str], prior: bool, undone: EvaluationResult,
        marginal: EvaluationResult,
    ) -> None:
        e_c = _pct(undone.metrics["energy_per_inference"].value, nm["energy_per_inference"].value)
        p_c = _pct(undone.metrics["power_avg"].value, nm["power_avg"].value)
        dtemp = nm["temperature"].value - undone.metrics["temperature"].value
        conf, why_conf = _confidence(best, prior)
        out.append(
            Advice(
                id=key, tier=tier, title=title, action=action,  # type: ignore[arg-type]
                gain=_gain(e_c, p_c, dtemp, undone.solver.throttled),
                why=why, tradeoffs=tradeoffs, confidence=conf,  # type: ignore[arg-type]
                confidence_reason=why_conf, effort=_effort_label(effort_score),  # type: ignore[arg-type]
                validate_steps=validate, in_recommended=True, fixes=fixes_of(marginal),
                effect={
                    "energy_saving_percent": e_c,
                    "power_saving_percent": p_c,
                    "temperature_change_c": dtemp,
                    "alone_energy_saving_percent": _pct(
                        bm["energy_per_inference"].value,
                        marginal.metrics["energy_per_inference"].value,
                    ),
                },
            )
        )

    if nc.precision != bc.precision:
        p, q = nc.precision, bc.precision
        ratio = hw.peak_tflops.get(p, 0) / hw.peak_tflops.get(q, 1) if hw.peak_tflops.get(q) else 0
        int8 = p == "int8"
        add(
            "precision", "model",
            f"Run the model in {p.upper()} instead of {q.upper()}",
            f"Export/quantise the model to {p.upper()} (TensorRT or ONNX Runtime).",
            f"{p.upper()} moves fewer bytes per weight and this chip has {ratio:.1f}× the peak "
            f"{p.upper()} throughput of {q.upper()}, so the same work finishes sooner and the "
            "chip can idle more." if ratio else
            f"{p.upper()} moves fewer bytes per weight, so the same work finishes sooner.",
            PRECISION_EFFORT.get((q, p), 2.0),
            (["Accuracy can drop; it needs calibration data and a check",
              "Needs INT8 support in your runtime"] if int8
             else ["Accuracy change is usually negligible (a few hundredths of a percent)"]),
            (["Calibrate with ~100–500 real images", "Compare accuracy with your validation set",
              "Benchmark on the device (Verify tab)"] if int8
             else ["Compare accuracy on your validation set", "Benchmark on the device"]),
            int8 and nm["accuracy"].provenance != Provenance.MEASURED,
            without(precision=q), alone(precision=p),
        )
    if nc.sparsity != bc.sparsity:
        add(
            "pruning", "model", f"Prune about {nc.sparsity:.0%} of the network",
            "Apply structured (channel) pruning, then fine-tune to recover accuracy.",
            "Removing whole channels shrinks compute and memory traffic roughly in proportion, "
            "so each frame needs less time and energy.",
            6.0,
            ["Needs fine-tuning (retraining) to win the accuracy back", "More engineering work"],
            ["Prune and fine-tune", "Measure accuracy on your validation set",
             "Benchmark on the device"],
            nm["accuracy"].provenance != Provenance.MEASURED,
            without(sparsity=bc.sparsity), alone(sparsity=nc.sparsity),
        )
    if nc.resolution_scale != bc.resolution_scale:
        add(
            "resolution", "model",
            f"Feed smaller images ({nc.resolution_scale:.0%} of native size)",
            "Resize inputs before inference and re-check accuracy.",
            "Compute and memory traffic scale with the square of the image side, so smaller "
            "inputs cost far less per frame.",
            3.0, ["Small objects may be missed", "Accuracy must be re-validated"],
            ["Run your validation set at the new size", "Benchmark on the device"],
            nm["accuracy"].provenance != Provenance.MEASURED,
            without(resolution_scale=bc.resolution_scale), alone(resolution_scale=nc.resolution_scale),
        )
    if nc.batch != bc.batch:
        add(
            "batch", "software", f"Process {nc.batch} frames at a time (batch {nc.batch})",
            f"Set the runtime batch size to {nc.batch}.",
            "Batching reuses the weights for several frames and keeps the chip busier, so "
            "energy per frame falls.",
            EFFORT_BATCH,
            [f"Worst-case frame latency becomes {nm['latency'].value:.0f} ms (frames wait for the batch)"],
            ["Check the latency limit still holds in your pipeline"], False,
            without(batch=bc.batch), alone(batch=nc.batch),
        )
    if nc.power_mode_id != bc.power_mode_id:
        add(
            "power_mode", "configure", f"Use the {hw.mode(nc.power_mode_id).name} power mode",
            f"Set the board's power mode to {hw.mode(nc.power_mode_id).name}.",
            "Lower clocks and voltage cost much less power, and at your frame rate the chip has "
            "spare time, so energy per frame falls faster than speed does.",
            EFFORT_POWER_MODE, ["Lower peak speed (fine while you keep the frame rate)"],
            ["Set the mode on the device", "Confirm the frame rate is still met"], False,
            without(power_mode_id=bc.power_mode_id), alone(power_mode_id=nc.power_mode_id),
        )
    if nc.clock_trim != bc.clock_trim:
        add(
            "clock", "configure", f"Cap the clock at {nc.clock_trim:.0%}",
            "Lock the GPU/memory clocks a little lower (DVFS).",
            "Dynamic power grows faster than speed with clock, so a small cap saves a lot.",
            EFFORT_CLOCK, ["Slightly lower peak speed"],
            ["Set clocks on the device", "Confirm the frame rate is still met"], False,
            without(clock_trim=bc.clock_trim), alone(clock_trim=nc.clock_trim),
        )
    if nc.cooling_id != bc.cooling_id:
        was = bm["temperature_unthrottled"].value
        now = nm["temperature_unthrottled"].value
        add(
            "cooling", "select", f"Upgrade the cooling to {hw.cooling(nc.cooling_id).name.lower()}",
            "Fit the larger heatsink / fan.",
            f"Without better cooling the chip would reach about {was:.0f} °C and throttle; "
            f"with it the same work settles near {now:.0f} °C.",
            EFFORT_COOLING, ["A physical part: cost, size and weight"],
            ["Fit it and log temperature with tegrastats under sustained load"], False,
            without(cooling_id=bc.cooling_id), alone(cooling_id=nc.cooling_id),
        )
    return out


def alternative_advice(alts: list[Alternative], base: EvaluationResult) -> list[Advice]:
    out: list[Advice] = []
    for kind, tier, label in (("model", "model", "Switch to a lighter model"),
                              ("device", "select", "Run it on different hardware")):  # fmt: skip
        pool = [
            a for a in alts
            if a.kind == kind and a.feasibility in (*CERTIFIED, Feasibility.UNCERTAIN)
            and (a.energy_saving_percent or 0) >= 5
            and (kind != "model" or a.lighter)
        ]  # fmt: skip
        if not pool:
            continue
        a = max(pool, key=lambda x: (x.feasibility in CERTIFIED, x.energy_saving_percent or 0))
        if kind == "model":
            trade = [f"Accuracy is about {(a.accuracy_retention or 0) * 100:.0f}% of the current model's "
                     "(from published results)"]  # fmt: skip
            why = (
                f"{a.name} needs far less compute per frame, so the same device does the job on "
                "less energy; its published accuracy still clears your accuracy requirement."
            )
            val = ["Fine-tune/evaluate it on your own data", "Benchmark on the device"]
            conf, reason = "medium", "accuracy is from published results, performance is predicted"
            eff = EFFORT_MODEL_SWAP
        else:
            trade = ["Different hardware to buy, integrate and qualify"]
            why = (
                f"{a.name} meets your requirements at lower energy for this workload "
                f"(predicted {a.power_w:.1f} W, {a.latency_ms:.0f} ms, {a.temperature_c:.0f} °C)."
            )
            val = ["Benchmark on a development kit before committing"]
            conf, reason = "low", "no measurements exist for that device; this is a physics prediction"
            eff = EFFORT_NEW_DEVICE
        out.append(
            Advice(
                id=f"{kind}:{a.id}", tier=tier, title=f"{label}: {a.name}",  # type: ignore[arg-type]
                action=("Use " if kind == "model" else "Build on ") + a.name + ".",
                gain=f"−{a.energy_saving_percent:.0f}% energy", why=why, tradeoffs=trade,
                confidence=conf, confidence_reason=reason,  # type: ignore[arg-type]
                effort=_effort_label(eff), validate_steps=val, in_recommended=False,  # type: ignore[arg-type]
                effect={"energy_saving_percent": a.energy_saving_percent,
                        "accuracy_retention": a.accuracy_retention},
            )
        )
    return out


def design_advice(whatifs: list[WhatIf], limit: str | None, max_items: int = 3) -> list[Advice]:
    out: list[Advice] = []
    ranked = sorted(
        whatifs,
        key=lambda w: (not w.makes_baseline_feasible,
                       -max(abs(w.energy_change_percent), abs(w.latency_change_percent))),
    )  # fmt: skip
    for w in ranked:
        if max(abs(w.energy_change_percent), abs(w.latency_change_percent)) < 2.0:
            continue
        if len(out) >= max_items:
            break
        if abs(w.energy_change_percent) >= abs(w.latency_change_percent):
            gain = f"{w.energy_change_percent:+.0f}% energy"
        else:
            gain = f"{w.latency_change_percent:+.0f}% latency"
        pct = abs(w.factor - 1.0) * 100.0
        why = (
            f"Changing just this by {pct:.0f}% would change energy per frame by "
            f"{w.energy_change_percent:+.0f}% and the time per frame by "
            f"{w.latency_change_percent:+.0f}%, with everything else unchanged."
        )
        if w.makes_baseline_feasible:
            why += " It would also make your current setup meet the requirements."
        out.append(
            Advice(
                id=f"design:{w.id}", tier="design", title=f"Chip/board idea: {w.title}",
                action=w.change, gain=gain, why=why,
                tradeoffs=["Design effort, area and cost", "Analytical what-if only"],
                confidence="low",
                confidence_reason="a model what-if on one parameter; it cannot be tested without a chip",
                effort="high", validate_steps=[w.verify_with], in_recommended=False,
                effect={"energy_change_percent": w.energy_change_percent,
                        "latency_change_percent": w.latency_change_percent,
                        "temperature_change_c": w.temperature_change_c},
            )
        )
    return out


def sort_advice(items: list[Advice]) -> list[Advice]:
    return sorted(items, key=lambda a: (TIER_ORDER[a.tier], not a.in_recommended))


def build_advice(
    ev: Ev,
    hw: HardwareProfile,
    baseline: EvaluationResult,
    best: EvaluationResult | None,
    alternatives: list[Alternative],
    whatifs: list[WhatIf],
    limit: str | None,
) -> list[Advice]:
    items: list[Advice] = []
    if best is not None:
        items += knob_advice(ev, hw, baseline, best)
    items += alternative_advice(alternatives, baseline)
    items += design_advice(whatifs, limit)
    return sort_advice(items)
