import type { Advice, Feasibility, Metric, Tier } from "../types";

export const SOURCE_WORDS: Record<Metric["provenance"], string> = {
  measured: "Measured",
  calibrated: "Predicted · checked against real runs",
  analytical: "Predicted",
  surrogate: "Predicted",
  simulated: "Simulated",
  vendor_spec: "From published specs",
  assumed: "Rough estimate",
  extracted_unverified: "From a datasheet, unconfirmed",
  synthetic_example: "Example data",
};

export const CONFIDENCE_WORDS: Record<Advice["confidence"], { label: string; hint: string }> = {
  high: { label: "Backed by real data", hint: "Checked against real measurements" },
  medium: { label: "Predicted", hint: "A physics prediction, not yet measured" },
  low: { label: "Rough estimate", hint: "Needs a real test before you rely on it" },
};

export const EFFORT_WORDS: Record<Advice["effort"], string> = {
  low: "Easy",
  medium: "Some work",
  high: "Big job",
};

export const LEVELS: { id: string; tiers: Tier[]; title: string; subtitle: string; accent: string }[] = [
  {
    id: "model",
    tiers: ["model"],
    title: "Change the AI model",
    subtitle: "Make the AI itself cheaper to run: smaller numbers, less to compute, or a lighter model.",
    accent: "var(--lvl-model)",
  },
  {
    id: "run",
    tiers: ["software", "configure"],
    title: "Change how you run it",
    subtitle: "Software and settings on the hardware you already have.",
    accent: "var(--lvl-run)",
  },
  {
    id: "hardware",
    tiers: ["select"],
    title: "Choose better hardware",
    subtitle: "A different board, or better cooling.",
    accent: "var(--lvl-hw)",
  },
  {
    id: "design",
    tiers: ["design"],
    title: "Redesign the chip or board",
    subtitle: "What-if ideas for hardware engineers: what would help if you could change the design.",
    accent: "var(--lvl-design)",
  },
];

export const STATE_PLAIN: Record<Feasibility, string> = {
  verified_feasible: "Works (verified)",
  predicted_feasible: "Works (predicted)",
  uncertain: "Probably works",
  infeasible: "Does not work",
  verified_infeasible: "Does not work (verified)",
};

export function fmt(n: number, digits?: number): string {
  if (!Number.isFinite(n)) return "–";
  if (digits !== undefined) return n.toFixed(digits);
  const a = Math.abs(n);
  if (a >= 100) return n.toFixed(0);
  if (a >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

export function money(n: number): string {
  return Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2);
}

export const GLOSSARY: Record<string, string> = {
  fps: "Frames per second: how many images the AI can process each second.",
  latency: "How long one frame takes from arrival to answer, in milliseconds.",
  throttle: "When a chip gets too hot it slows itself down to cool off. That makes everything slower.",
  quantise: "Storing the AI's numbers with fewer digits (for example 16-bit instead of 32-bit). It is faster and cheaper, with a small accuracy risk.",
  pruning: "Removing parts of the network that matter least, then re-training so accuracy recovers.",
  batch: "Processing several frames at once. More efficient, but each frame waits for the others.",
  dvfs: "Running the chip at a lower speed and voltage, which saves much more power than it costs in speed.",
};
