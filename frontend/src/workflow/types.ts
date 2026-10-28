/** Wire types for the workflow stages (backend: app/services/workflow_service.py). */

export type StageState = "done" | "ready" | "blocked" | "stale";
export interface Stage {
  id: "input" | "verification" | "analysis" | "recommendations" | "validation" | "results";
  title: string;
  state: StageState;
  depends_on?: string[];
  record: Record<string, unknown> | null;
  summary: string;
}
export interface Pipeline {
  schema: string;
  project_id: number;
  project_version: number;
  inputs_hash: string;
  stages: Stage[];
  next: string | null;
  rule: string;
}

export type CheckStatus = "pass" | "warn" | "fail" | "info";
export interface Check { id: string; area: string; status: CheckStatus; message: string; fix: string }
export interface VerificationReport {
  schema: string;
  status: "pass" | "warn" | "fail";
  ready_for_analysis: boolean;
  inputs: { model_id: string; device_id: string; cooling_id: string | null; project_version: number | null };
  checks: Check[];
  missing: string[];
  counts: Record<CheckStatus, number>;
  note: string;
}

export type RecStatus = "recommended" | "option" | "directional" | "guidance" | "needs_input" | "ai_candidate";
export interface RecValidation {
  state: "not_evaluated" | "evaluated" | "rejected" | "measured";
  plan_id?: number;
  plan_name?: string;
  plan_version?: number;
  stale?: boolean;
  feasibility?: string;
  energy_change_percent?: number | null;
  method?: string;
}
export interface Rec {
  id: string;
  category: "software" | "hardware";
  area: string;
  area_label: string;
  title: string;
  problem: string;
  change?: string;
  why: string;
  affects: string[];
  expected?: Record<string, number>;
  benefit?: string;
  tradeoffs: string[];
  requires: string[];
  status: RecStatus;
  confidence?: string;
  evidence?: string;
  assumptions?: string[];
  fixes?: string[];
  plan_change_id: string | null;
  in_recommended?: boolean;
  rank?: number;
  source?: "engine" | "ai";
  notes?: string[];
  validation: RecValidation;
}
export interface AiCandidates {
  schema: string;
  model: string;
  label: string;
  summary: string;
  candidates: Rec[];
  dropped: { title: string; reason: string }[];
  sent?: string[];
  cached?: boolean;
  usage?: { total_tokens?: number };
}
export interface RecommendationSet {
  schema: string;
  analysed: boolean;
  message?: string;
  priority?: string;
  baseline?: { feasibility: string; bottleneck: string; failing: string[]; provenance: string };
  software: Rec[];
  hardware: Rec[];
  statuses?: Record<string, string>;
  source_run_id?: number;
  design_files?: string[];
  stale?: boolean;
  ai: AiCandidates | null;
}

export interface TermsStatus {
  current: Record<string, string>;
  outstanding: string[];
  accepted: { document: string; version: string; accepted_at: string }[];
}
export interface LegalDocInfo { id: string; title: string; version: string; requires_acceptance: boolean }
