import type { AreaId } from "./router";

/** The one path through a project, in order. The stepper at the top, the side navigation, the
 * sub-step row on each page and the Back / Next buttons all read from this list, so every place
 * the user looks names the same step the same way.
 *
 * Stages are the six workflow stages the server tracks (input .. results). Steps are the pages, in
 * the order a beginner should visit them. Side views add detail to a step and are optional: they
 * sit under "More" and never sit on the Back / Next path. */

export interface Step {
  id: string;
  stage: number;
  title: string;
  /** What to do on this page, in one plain sentence. */
  todo: string;
  area: AreaId;
  tab?: string;
  /** Can be skipped: shown as "optional" on the path. */
  optional?: boolean;
}

export interface SideView {
  id: string;
  /** The step this view belongs to. */
  parent: string;
  title: string;
  area: AreaId;
  tab?: string;
}

export const STAGES = ["Inputs", "Verification", "Analysis", "Recommendations", "Validation", "Results and export"] as const;

export const STEPS: Step[] = [
  { id: "input", stage: 1, title: "Model and hardware", area: "input",
    todo: "Choose the AI model and the hardware it will run on, or upload your own." },
  { id: "constraints", stage: 1, title: "Requirements and environment", area: "constraints",
    todo: "Say what it must achieve (speed, power, temperature) and where it will run." },
  { id: "check", stage: 2, title: "Check the inputs", area: "check",
    todo: "Fix anything flagged, then run the analysis." },
  { id: "summary", stage: 3, title: "Analysis", area: "summary",
    todo: "See how your setup performs today and what limits it. Nothing is changed yet." },
  { id: "engine", stage: 4, title: "Try changes", area: "recommendations", tab: "engine",
    todo: "Pick a suggested change to see the analysis re-run with it." },
  { id: "plan", stage: 5, title: "Build and compare a plan", area: "recommendations", tab: "plan",
    todo: "Combine the changes you want, compare them with today, and save the plan." },
  { id: "saved", stage: 5, title: "Saved plans", area: "recommendations", tab: "saved",
    todo: "Review the plans you saved; each version is kept unchanged." },
  { id: "run", stage: 5, title: "Run on real hardware", area: "verify", tab: "run", optional: true,
    todo: "Optional: run the model on a connected server to measure it for real." },
  { id: "measured", stage: 5, title: "Predicted vs measured", area: "verify", tab: "verify", optional: true,
    todo: "Optional: compare the predictions with real measurements." },
  { id: "export", stage: 6, title: "Export", area: "verify", tab: "export",
    todo: "Download a report or a plan to hand over." },
]; // fmt: skip

export const SIDE_VIEWS: SideView[] = [
  { id: "system-map", parent: "summary", title: "System map", area: "system-map" },
  { id: "timeline", parent: "summary", title: "Execution timeline", area: "timeline" },
  { id: "memory", parent: "summary", title: "Memory map", area: "memory" },
  { id: "thermal", parent: "summary", title: "Thermal and cooling", area: "thermal" },
  { id: "energy", parent: "summary", title: "Energy and performance", area: "energy" },
  { id: "results", parent: "engine", title: "The recommended plan", area: "recommendations", tab: "results" },
  { id: "catalogue", parent: "engine", title: "Every recommendation, in full", area: "recommendations", tab: "catalogue" },
  { id: "cooling", parent: "engine", title: "Cooling options", area: "recommendations", tab: "cooling" },
  { id: "history", parent: "export", title: "History", area: "verify", tab: "history" },
]; // fmt: skip

/** Default tab of an area when the link names none. */
const DEFAULT_TAB: Partial<Record<AreaId, string>> = { recommendations: "engine", verify: "run" };

export function hrefOf(projectId: number, x: { area: AreaId; tab?: string }): string {
  return `#/p/${projectId}/${x.area}${x.tab ? `?tab=${x.tab}` : ""}`;
}

/** Where the user is: the step (or the step a side view belongs to) and the side view, if any. */
export function locate(area: AreaId | null, tab: string | null): { step: Step | null; side: SideView | null } {
  if (!area) return { step: null, side: null };
  const t = tab ?? DEFAULT_TAB[area] ?? undefined;
  const step = STEPS.find((s) => s.area === area && s.tab === t) ?? null;
  if (step) return { step, side: null };
  const side = SIDE_VIEWS.find((v) => v.area === area && v.tab === t) ?? null;
  return { step: side ? STEPS.find((s) => s.id === side.parent) ?? null : null, side };
}

export function neighbours(step: Step): { back: Step | null; next: Step | null } {
  const i = STEPS.indexOf(step);
  return { back: STEPS[i - 1] ?? null, next: STEPS[i + 1] ?? null };
}
