"""Default constants for the L0 simulator.

Every value here is an ASSUMPTION until calibrated against measurements (Day 3).
They are documented in docs/assumptions.md and surfaced with provenance "assumed".
"""

ENGINE_VERSION = "0.3.0-l0"

BYTES_PER_ELEM = {"fp32": 4.0, "fp16": 2.0, "int8": 1.0}

# Fraction of peak compute actually achieved, per precision.
ETA_COMPUTE = {"fp32": 0.45, "fp16": 0.35, "int8": 0.25}
# Fraction of peak memory bandwidth achieved.
ETA_MEMORY = 0.70
# Imperfect overlap of compute and memory: t = max + KAPPA*min + launch overhead.
KAPPA = 0.15
LAUNCH_OVERHEAD_S = 10e-6  # per layer

# Live activation fraction of total activation traffic and fixed runtime footprint.
LIVE_ACT_FRACTION = 0.30
RUNTIME_OVERHEAD_GB = 0.35
USABLE_MEM_FRACTION = 0.85

# Uncertainty is propagated by re-solving the whole coupled model at an optimistic and a
# pessimistic corner of these parameter ranges (an envelope, not a statistical interval;
# its coverage is validated against measurements on Day 3).
CORNER_ETA = 0.25  # +/- on achieved compute and memory efficiency
CORNER_POWER = 0.15  # +/- on every power term
CORNER_RTH = 0.20  # +/- on thermal resistance
# Manual-parameter models carry less information, so their corner widths are scaled up.
FIDELITY_WIDEN = {"library": 1.0, "graph": 1.0, "manual": 1.5}

# Leakage grows with temperature: P_leak(T) = P_leak(25C) * exp(K_LEAK * (T - 25)).
K_LEAK = 0.015

# Thermal governor / coupled solver.
GOVERNOR_MIN_CLOCK = 0.10  # lowest clock multiplier the governor may apply
SOLVER_MAX_ITERS = 60
SOLVER_TOL_C = 0.01
SOLVER_DAMPING = 0.7
BISECT_ITERS = 14
RUNAWAY_TEMP_C = 250.0
REPORT_TEMP_CAP_C = 200.0  # unthrottled temperatures are shown clamped to this

# Resolution scaling: retention = 1 - slope * (1 - scale). Priors, not measurements.
RES_RETENTION_SLOPE = 0.30
RES_RETENTION_SLOPE_LOW = 0.60

# Structured pruning prior: retention = 1 - slope * sparsity (before any fine-tuning). Priors.
PRUNE_RETENTION_SLOPE = 0.15
PRUNE_RETENTION_SLOPE_LOW = 0.40

# Accuracy retained vs the fp32 baseline: (value, low, high). Priors, not measurements.
ACCURACY_RETENTION_PRIOR = {
    "fp32": (1.000, 1.000, 1.000),
    "fp16": (0.998, 0.990, 1.000),
    "int8": (0.985, 0.930, 1.000),
}
# Precisions whose accuracy effect must be measured before they can be called feasible.
UNMEASURED_CAPPED_PRECISIONS = {"int8"}

# Calibration (engine/calibration.py).
CALIB_MIN_POINTS = 3  # leave-one-out needs at least this many measurements
CALIB_SHRINK_K = 1.0  # factor = exp(mean_log * n / (n + K)): few points pull weakly from 1.0
CALIB_ACCEPT_RATIO = 0.9  # accept only if leave-one-out error falls to <= 90% of uncalibrated
CALIB_WIDTH_MARGIN = 1.15  # empirical band half-width = max LOO error x margin
CALIB_WIDTH_MIN = 0.03
CALIB_WIDTH_MAX = 0.60
ETA_SOLVE_MIN, ETA_SOLVE_MAX = 0.05, 3.0
