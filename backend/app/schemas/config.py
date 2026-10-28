from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Config(BaseModel):
    """One point in the design space: software knobs plus configurable hardware knobs."""

    precision: Literal["fp32", "fp16", "int8"]
    batch: int
    power_mode_id: str
    cooling_id: str
    # Input resolution relative to the model's native size (software knob).
    resolution_scale: float = Field(1.0, gt=0, le=1.0)
    # Extra clock multiplier on top of the power mode (hardware "configure" knob).
    clock_trim: float = Field(1.0, gt=0, le=1.0)
    # Fraction of the model's weights/channels removed by structured pruning (model knob).
    sparsity: float = Field(0.0, ge=0, le=0.8)
