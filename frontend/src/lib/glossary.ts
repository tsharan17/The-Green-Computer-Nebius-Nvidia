/** Plain-language translations of every technical concept the product shows.
 * `plain` replaces the jargon in Simple mode; `why` answers "Why does this matter?". The precise
 * term is always kept so experts can match it to the engineering views and exports. */
export interface Concept {
  term: string;
  plain: string;
  why: string;
}

export const GLOSSARY: Record<string, Concept> = {
  "compute-bound": {
    term: "Compute-bound",
    plain: "The chip is busy calculating most of the time.",
    why: "More compute (a faster chip, a lower-precision number format, a smaller model) helps; faster memory would not.",
  },
  "memory-bound": {
    term: "Memory-bound",
    plain: "The chip spends much of its time waiting for data from memory.",
    why: "Moving fewer bytes helps most: lower precision, smaller inputs, fused operations or faster memory. Extra compute would sit idle.",
  },
  "overhead-bound": {
    term: "Overhead-bound",
    plain: "Most time goes into starting many small operations, not into the work itself.",
    why: "A runtime that fuses operations (TensorRT, OpenVINO, XNNPACK) or batching helps more than a faster chip.",
  },
  heat: {
    term: "Thermally limited",
    plain: "The chip gets too hot and slows itself down to cool off.",
    why: "Better cooling or less power lets it keep its full speed; otherwise performance drops over time.",
  },
  throttling: {
    term: "Thermal throttling",
    plain: "When the chip reaches its temperature limit, it lowers its own speed to avoid damage.",
    why: "A setup that throttles is slower in real use than a short test suggests, and it ages the hardware faster.",
  },
  latency: {
    term: "Latency",
    plain: "How long one input takes, from arrival to answer.",
    why: "Matters for anything interactive or safety-related: a robot must react before it moves on.",
  },
  throughput: {
    term: "Throughput",
    plain: "How many inputs per second the system can finish.",
    why: "If inputs arrive faster than this, they queue up or are dropped.",
  },
  batch: {
    term: "Batch size",
    plain: "How many inputs are processed together in one go.",
    why: "Bigger batches use the chip more efficiently but make each input wait for the others.",
  },
  precision: {
    term: "Number format (precision)",
    plain: "How many bits each number in the model uses: FP32 (most exact), FP16, INT8 (smallest).",
    why: "Smaller formats run faster and use less energy and memory, but can lower accuracy; INT8 must be checked on real data.",
  },
  quantisation: {
    term: "Quantisation",
    plain: "Converting the model to a smaller number format such as INT8.",
    why: "Usually the largest single speed and energy gain on edge hardware, at a measurable accuracy risk.",
  },
  pruning: {
    term: "Structured pruning",
    plain: "Removing whole parts of the network that contribute least.",
    why: "Cuts work roughly in proportion, but the model must be retrained to win back accuracy.",
  },
  "power-mode": {
    term: "Power mode",
    plain: "A setting that caps how much power the board may draw (and so its clock speed).",
    why: "A lower mode often meets the frame rate on much less power, because the chip had spare time anyway.",
  },
  "thermal-resistance": {
    term: "Thermal resistance (K/W)",
    plain: "How many degrees the chip warms up for each watt it uses, with this cooling.",
    why: "Lower is better: a 2 K/W heatsink at 10 W adds about 20 °C above the room temperature.",
  },
  ambient: {
    term: "Ambient temperature",
    plain: "The temperature of the air around the device.",
    why: "Every degree of ambient is a degree less headroom before throttling.",
  },
  leakage: {
    term: "Leakage power",
    plain: "Power the chip loses just by being switched on, which grows as it gets hotter.",
    why: "A hot chip wastes more power, which makes it hotter still; good cooling breaks that loop.",
  },
  energy: {
    term: "Energy per input",
    plain: "The electricity used to process one input, including idle time between inputs.",
    why: "Sets battery life, running cost and carbon footprint for a given workload.",
  },
  band: {
    term: "Uncertainty band",
    plain: "The range a value is expected to fall in: best case to worst case.",
    why: "Requirements are checked against the worst case, so a 'pass' still holds if reality is pessimistic.",
  },
  provenance: {
    term: "Source of a number (provenance)",
    plain: "Where a value comes from: predicted by the model, calibrated, measured, a vendor specification, or assumed.",
    why: "A measured value can be trusted more than a prediction; every number says which it is.",
  },
  calibration: {
    term: "Calibration",
    plain: "Adjusting predictions using real measurements of the same device.",
    why: "Brings predictions closer to reality, but is only accepted when it predicts held-out measurements better.",
  },
  "working-memory": {
    term: "Working memory",
    plain: "The memory the model needs at its busiest moment: weights plus the data in flight.",
    why: "If it exceeds what the device can use, the model will not run at all.",
  },
  bandwidth: {
    term: "Memory bandwidth",
    plain: "How fast data can move between memory and the chip (GB/s).",
    why: "When a model moves a lot of data, this, not compute, sets the speed.",
  },
  "tensor-lifetime": {
    term: "Tensor lifetime",
    plain: "How long an intermediate result must stay in memory, from when it is made to when it is last read.",
    why: "Long-lived large results drive the memory peak; reordering or fusing operations can shorten them.",
  },
  feasibility: {
    term: "Outcome",
    plain: "Pass means every requirement holds even in the worst case; uncertain means it depends on something not yet measured; fails means at least one requirement is missed.",
    why: "Tells you whether you can build on this setup, need to test something first, or must change it.",
  },
  directional: {
    term: "Directional what-if",
    plain: "An idea for changing the chip or board, with its effect estimated by the model only.",
    why: "Useful to decide what to explore; it is not a validated design and needs real design tools to confirm.",
  },
};

export const BOTTLENECK_PLAIN: Record<string, string> = {
  compute: GLOSSARY["compute-bound"].plain,
  memory: GLOSSARY["memory-bound"].plain,
  overhead: GLOSSARY["overhead-bound"].plain,
  heat: GLOSSARY.heat.plain,
};

export const OUTCOME: Record<string, { label: string; icon: string; cls: string; plain: string }> = {
  verified_feasible: { label: "Passes (measured)", icon: "✓", cls: "ok", plain: "Meets every requirement, backed by measurements." },
  predicted_feasible: { label: "Passes", icon: "✓", cls: "ok", plain: "Meets every requirement, even in the worst case predicted." },
  uncertain: { label: "Uncertain", icon: "?", cls: "warn", plain: "Probably works, but something it depends on has not been measured yet." },
  infeasible: { label: "Fails", icon: "✕", cls: "bad", plain: "Misses at least one requirement." },
  verified_infeasible: { label: "Fails (measured)", icon: "✕", cls: "bad", plain: "Measurements show it misses a requirement." },
};
