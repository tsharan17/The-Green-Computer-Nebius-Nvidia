from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, model_validator


class Provenance(str, Enum):
    MEASURED = "measured"
    CALIBRATED = "calibrated"
    ANALYTICAL = "analytical"
    SURROGATE = "surrogate"
    SIMULATED = "simulated"
    VENDOR_SPEC = "vendor_spec"
    ASSUMED = "assumed"
    EXTRACTED_UNVERIFIED = "extracted_unverified"
    SYNTHETIC_EXAMPLE = "synthetic_example"


class Feasibility(str, Enum):
    VERIFIED_FEASIBLE = "verified_feasible"
    PREDICTED_FEASIBLE = "predicted_feasible"
    UNCERTAIN = "uncertain"
    INFEASIBLE = "infeasible"
    VERIFIED_INFEASIBLE = "verified_infeasible"


class Metric(BaseModel):
    """A number that always carries its unit, uncertainty band and origin."""

    value: float
    unit: str
    low: float
    high: float
    provenance: Provenance

    @model_validator(mode="after")
    def _ordered(self) -> Metric:
        if not (self.low <= self.value <= self.high):
            raise ValueError(
                f"Metric band must satisfy low <= value <= high, got "
                f"{self.low} <= {self.value} <= {self.high}"
            )
        return self

    @classmethod
    def exact(cls, value: float, unit: str, provenance: Provenance) -> Metric:
        return cls(value=value, unit=unit, low=value, high=value, provenance=provenance)

    @classmethod
    def rel(cls, value: float, unit: str, provenance: Provenance, rel: float) -> Metric:
        """Symmetric relative band, value*(1-rel) .. value*(1+rel)."""
        return cls(
            value=value,
            unit=unit,
            low=value * (1 - rel),
            high=value * (1 + rel),
            provenance=provenance,
        )
