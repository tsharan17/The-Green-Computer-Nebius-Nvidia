"""Describing and costing the difference between two configurations."""

from __future__ import annotations

from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile

# Rough engineering effort of changing each knob (relative units, used to rank repairs).
PRECISION_EFFORT = {
    ("fp32", "fp16"): 2.0,
    ("fp32", "int8"): 4.0,
    ("fp16", "int8"): 3.0,
    ("int8", "fp16"): 1.0,
    ("fp16", "fp32"): 1.0,
    ("int8", "fp32"): 1.0,
}
EFFORT_BATCH = 1.0
EFFORT_RESOLUTION = 3.0  # re-validate accuracy, may need retraining
EFFORT_PRUNING = 6.0  # pruning needs fine-tuning and a full accuracy check
EFFORT_POWER_MODE = 1.0
EFFORT_CLOCK = 1.0
EFFORT_COOLING = 4.0  # a physical part
EFFORT_MODEL_SWAP = 6.0  # new model: retrain/fine-tune and re-validate
EFFORT_NEW_DEVICE = 8.0  # new hardware to buy, integrate and qualify


def effort(base: Config, cand: Config) -> float:
    e = 0.0
    if cand.precision != base.precision:
        e += PRECISION_EFFORT.get((base.precision, cand.precision), 2.0)
    if cand.batch != base.batch:
        e += EFFORT_BATCH
    if cand.resolution_scale != base.resolution_scale:
        e += EFFORT_RESOLUTION
    if cand.sparsity != base.sparsity:
        e += EFFORT_PRUNING
    if cand.power_mode_id != base.power_mode_id:
        e += EFFORT_POWER_MODE
    if cand.clock_trim != base.clock_trim:
        e += EFFORT_CLOCK
    if cand.cooling_id != base.cooling_id:
        e += EFFORT_COOLING
    return e


def describe(base: Config, cand: Config, hw: HardwareProfile | None = None) -> list[str]:
    """Plain-text changes; with `hw`, power modes and cooling are shown by their real names."""

    def mode(i: str) -> str:
        return hw.mode(i).name if hw else i

    def cool(i: str) -> str:
        return hw.cooling(i).name if hw else i

    out = []
    if cand.precision != base.precision:
        out.append(f"Software: precision {base.precision.upper()} → {cand.precision.upper()}")
    if cand.sparsity != base.sparsity:
        out.append(f"AI model: prune {cand.sparsity:.0%} of the network (then fine-tune)")
    if cand.resolution_scale != base.resolution_scale:
        out.append(
            f"AI model: input resolution {base.resolution_scale:.0%} → {cand.resolution_scale:.0%}"
        )
    if cand.batch != base.batch:
        out.append(f"Software: batch {base.batch} → {cand.batch}")
    if cand.power_mode_id != base.power_mode_id:
        out.append(
            f"Hardware (configure): power mode {mode(base.power_mode_id)} → {mode(cand.power_mode_id)}"
        )
    if cand.clock_trim != base.clock_trim:
        out.append(f"Hardware (configure): clock {base.clock_trim:.0%} → {cand.clock_trim:.0%}")
    if cand.cooling_id != base.cooling_id:
        out.append(
            f"Hardware (select): cooling {cool(base.cooling_id)} → {cool(cand.cooling_id)}"
        )
    return out
