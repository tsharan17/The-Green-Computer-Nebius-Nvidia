export type Provenance =
  | "measured"
  | "calibrated"
  | "analytical"
  | "surrogate"
  | "simulated"
  | "vendor_spec"
  | "assumed"
  | "extracted_unverified"
  | "synthetic_example";

export type Feasibility =
  | "verified_feasible"
  | "predicted_feasible"
  | "uncertain"
  | "infeasible"
  | "verified_infeasible";

export interface Metric {
  value: number;
  unit: string;
  low: number;
  high: number;
  provenance: Provenance;
}

export interface Config {
  precision: "fp32" | "fp16" | "int8";
  batch: number;
  power_mode_id: string;
  cooling_id: string;
  resolution_scale: number;
  clock_trim: number;
  sparsity: number;
}

export interface SolverInfo {
  converged: boolean;
  iterations: number;
  throttled: boolean;
  clock_factor: number;
}

export interface ConstraintCheck {
  name: string;
  sense: "<=" | ">=";
  limit: number;
  unit: string;
  pessimistic: number;
  optimistic: number;
  status: "pass" | "fail" | "uncertain";
  note: string;
}

export interface EvaluationResult {
  config: Config;
  metrics: Record<string, Metric>;
  bottleneck: "compute" | "memory" | "overhead" | "heat";
  throttle_risk: "low" | "medium" | "high";
  feasibility: Feasibility;
  checks: ConstraintCheck[];
  notes: string[];
  solver: SolverInfo;
  time_to_throttle_s: number | null;
  thermal_trace: [number, number][];
  engine_version: string;
}

export interface Recommendation {
  title: string;
  changes: string[];
  summary: string;
  state: Feasibility;
  caveats: string[];
}

export interface Point {
  config: Config;
  state: Feasibility;
  energy_mj: number;
  latency_ms: number;
  accuracy: number;
  power_w: number;
  memory_gb: number;
  temperature_c: number;
  pareto: boolean;
  best: boolean;
}

export interface CoDesign {
  baseline: EvaluationResult;
  software_only: EvaluationResult | null;
  hardware_only: EvaluationResult | null;
  combined: EvaluationResult | null;
  interaction_factor: number | null;
  interpretation: string;
}

export type Tier = "model" | "software" | "configure" | "select" | "design";

export interface VerdictSide {
  state: Feasibility;
  ok: boolean;
  headline: string;
  problems: string[];
  power_w: number;
  latency_ms: number;
  temperature_c: number;
  capacity_fps: number;
  energy_mj: number;
  config: Config;
}

export interface Verdict {
  baseline: VerdictSide;
  improved: VerdictSide | null;
  savings: { power_percent: number; energy_percent: number; cost_per_year: number; carbon_kg_per_year: number } | null;
  fps_required: number;
  summary: string;
}

export interface RepairPlan {
  changes: string[];
  effort_score: number;
  feasibility: Feasibility;
  fixes: string[];
  config: Config;
  energy_saving_percent: number;
  note: string;
}

export interface Repair {
  needed: boolean;
  plans: RepairPlan[];
  binding_constraints: string[];
  message: string;
}

export interface Alternative {
  kind: "device" | "model";
  id: string;
  name: string;
  feasibility: Feasibility;
  config: Config;
  power_w: number;
  energy_mj: number;
  latency_ms: number;
  temperature_c: number;
  capacity_fps: number;
  energy_saving_percent: number | null;
  accuracy_retention: number | null;
  lighter: boolean | null;
  compared_with: string;
  problem: string | null;
  note: string;
}

export interface WhatIf {
  id: string;
  title: string;
  change: string;
  parameter: string;
  factor: number;
  latency_change_percent: number;
  energy_change_percent: number;
  power_change_percent: number;
  temperature_change_c: number;
  elasticity_latency: number;
  elasticity_energy: number;
  makes_baseline_feasible: boolean;
  verify_with: string;
}

export interface Advice {
  id: string;
  tier: Tier;
  title: string;
  action: string;
  gain: string;
  why: string;
  effect: Record<string, number | null>;
  tradeoffs: string[];
  confidence: "high" | "medium" | "low";
  confidence_reason: string;
  effort: "low" | "medium" | "high";
  validate_steps: string[];
  in_recommended: boolean;
  fixes: string[];
}

export interface OptimisationResult {
  baseline: EvaluationResult;
  best: EvaluationResult | null;
  top: EvaluationResult[];
  recommendation: Recommendation | null;
  codesign: CoDesign;
  points: Point[];
  evaluated: number;
  counts: Record<string, number>;
  search_seconds: number;
  message: string;
  verdict: Verdict | null;
  repair: Repair | null;
  advice: Advice[];
  alternatives: Alternative[];
  whatifs: WhatIf[];
  limiting_factor: string | null;
}

export interface Measurement {
  id: number;
  device_id: string;
  model_id: string;
  precision: "fp32" | "fp16" | "int8";
  batch: number;
  source: string;
  hardware_label: string;
  latency_ms_mean: number | null;
  throughput_fps: number | null;
  power_w_avg: number | null;
  temperature_c_max: number | null;
  accuracy_retention: number | null;
  notes: string;
}

export interface VerifyRow {
  measurement: Measurement;
  predicted_ms: number;
  predicted_cal_ms: number | null;
  error_before: number | null;
  error_after: number | null;
  inside_band: boolean | null;
}

export interface VerifySummary {
  device_id: string;
  precision: string;
  n: number;
  mape_before: number | null;
  mape_after: number | null;
  coverage: number | null;
  calibration_version: number | null;
  calibration_active: boolean;
  note: string;
}

export interface VerifyOut {
  rows: VerifyRow[];
  summaries: VerifySummary[];
}

export interface CalibrationHistoryItem {
  device_id: string;
  precision: string;
  version: number;
  accepted: boolean;
  created_at: string;
  latency: { n: number; error_before: number | null; error_after: number | null; accepted: boolean; reason: string };
  power: { n: number; accepted: boolean; reason: string };
}

export interface BenchmarkStatus {
  jobs_configured: boolean;
  detail: string;
  replay_available: boolean;
  cli_found?: boolean;
  import_formats: string[];
}

export interface Workload {
  fps: number;
  ambient_c: number;
  hours_per_day: number;
  price_per_kwh: number;
  grid_gco2_per_kwh: number;
  streaming: boolean;
}

export interface Constraints {
  max_power_w: number;
  min_accuracy: number;
  accuracy_mode: "relative" | "absolute";
  max_latency_ms: number | null;
  max_memory_gb: number | null;
  forbid_throttling: boolean;
}

export type Priority = "balanced" | "faster" | "lower_power" | "lower_heat" | "lower_cost" | "fit_memory";

export interface ProjectSpec {
  name: string;
  priority?: Priority;
  use_case?: string | null;
  model_id: string;
  device_id: string;
  cooling_id: string | null;
  baseline: Config | null;
  workload: Workload;
  constraints: Constraints;
  environment?: Environment;
}

export interface ProjectOut {
  id: number;
  spec: ProjectSpec;
  version: number;
  workspace_id: number | null;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface Workspace {
  id: number;
  name: string;
  personal: boolean;
  legacy: boolean;
  role: "owner" | "editor" | "viewer";
}

export interface Me {
  user: { id: number; email: string; display_name: string; created_at: string; email_verified?: boolean; mfa_enabled?: boolean; verification_required?: boolean };
  dev_verify_url?: string;
  workspaces: Workspace[];
  active_workspace_id: number | null;
  role: string | null;
  terms?: import("./workflow/types").TermsStatus;
}

export interface AuthConfig {
  signup_allowed: boolean;
  environment: string;
  email_delivery: string;
  password_min_length: number;
  demo_mode: boolean;
}

export interface VersionItem {
  id: number;
  version: number;
  spec: ProjectSpec;
  note: string;
  created_by: number | null;
  created_at: string;
}

export interface RunItem {
  id: number;
  kind: string;
  engine_version: string;
  version: number | null;
  created_at: string;
  user_id: number | null;
  summary: Record<string, unknown>;
}

export interface AuditItem {
  id: number;
  action: string;
  target_type: string;
  target_id: string;
  detail: Record<string, unknown>;
  user_id: number | null;
  created_at: string;
}

export interface ArtifactItem {
  id: number;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: "valid" | "invalid" | "needs_review" | "unsupported";
  validation: Record<string, unknown>;
  project_id: number | null;
  stored: boolean;
  uploaded_by: number | null;
  created_at: string;
}

export interface UsageSummary {
  period_start: string;
  this_period: Record<string, number>;
  all_time: Record<string, number>;
  units: Record<string, string>;
  storage_bytes_in_use: number;
  platform_cost: {
    rates: Record<string, number>;
    currency: string;
    lines: Record<string, number>;
    total: number;
    configured: boolean;
    note: string;
  };
  device_energy_note: string;
}

export interface SessionItem {
  id: number;
  kind: string;
  label: string;
  user_agent: string;
  ip: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface ModelProfile {
  id: string;
  name: string;
  task: string;
  input_desc: string;
  params_m: number;
  gflops: number;
  n_layers: number;
  accuracy_metric: string;
  baseline_accuracy: number;
  fidelity: "library" | "graph" | "manual";
  resolution_scalable: boolean;
  notes: string;
}

export interface UploadOut {
  profile: ModelProfile;
  warnings: string[];
}

export interface HardwareProfile {
  id: string;
  name: string;
  vendor?: string;
  verified?: boolean;
  kind: string;
  role: string;
  mem_gb: number;
  idle_w: number;
  mem_bw_gbs: number;
  dyn_w_max?: number;
  mem_w_max?: number;
  leak_w_25c?: number;
  t_throttle_c?: number;
  t_shutdown_c?: number;
  weights_mem_gb?: number | null;
  onchip_weights_mb?: number | null;
  notes?: string;
  sources?: Record<string, string>;
  extras?: Record<string, number | string>;
  cooling_options: { id: string; name: string; r_th_k_per_w: number; fan_w: number; c_th_j_per_k?: number }[];
  power_modes: { id: string; name: string; cap_w: number; freq_scale?: number; mem_scale?: number }[];
  peak_tflops: Record<string, number>;
  default_cooling: string;
}

export interface AgentStatus {
  configured: boolean;
  base_url: string;
  detail: string | null;
  models?: Record<string, string | null>;
  available_models?: number;
}

export type AgentEvent =
  | { type: "status"; text: string }
  | { type: "tool_call"; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "guardrail"; action: "rewrite" | "redacted"; figures: string[] }
  | { type: "message"; text: string; guardrail_ok: boolean }
  | { type: "error"; text: string }
  | { type: "done" };

export interface BenchmarkJob {
  id: number;
  job_name: string;
  job_id: string;
  device_id: string;
  model_id: string;
  precision: string;
  batches: number[];
  state: string;
  ingested: boolean;
  measurement_ids: number[];
  error: string | null;
  created_at: string;
}

export interface HardwareDraft {
  id: number;
  query: string;
  status: "draft" | "confirmed" | "rejected";
  candidate: {
    name?: string;
    vendor?: string;
  verified?: boolean;
    peak_tflops?: Record<string, number>;
    mem_bw_gbs?: number;
    mem_gb?: number;
    power_modes?: { name: string; cap_w: number }[];
    tdp_w?: number;
    t_throttle_c?: number;
  };
  evidence: { field: string; value: unknown; source_url: string | null }[];
  missing: string[];
  device_id: string | null;
  needs_confirmation: boolean;
  notes?: string;
  dropped_implausible?: string[];
}

export interface RecipeOut {
  files: Record<string, { description: string; content: string }>;
  warnings: string[];
}

export interface LayerRow {
  index: number;
  name: string;
  op: string;
  time_ms: number;
  share: number;
  bound: "compute" | "memory";
  flops_share: number;
  weights_kb: number;
  quant_risk: number;
}

export interface LayerProfile {
  has_graph: boolean;
  graph_source?: string;
  note: string;
  total_ms: number;
  overhead_ms?: number;
  n_layers?: number;
  layers: LayerRow[];
  top?: LayerRow[];
  by_op: { op: string; time_ms: number; count: number; share: number }[];
  fragile?: LayerRow[];
  hints?: string[];
}

export type HardwareStyle = "generic" | "mcu" | "fpga" | "accelerator";

export interface ReportParse {
  tool: string;
  fields?: Record<string, number>;
  found?: Record<string, unknown>;
  note?: string;
  power_w_avg?: number;
  power_w_active?: number;
  power_w_idle?: number;
  power_w_peak?: number;
  samples?: number;
}

export interface FileFormat {
  id: string;
  name: string;
  extensions: string[];
  status: "supported" | "planned" | "unsupported";
  gives: string;
  required: string;
}

export interface FormatCatalogue {
  model: FileFormat[];
  categories: { id: string; name: string; kinds: string[]; examples: string; formats: FileFormat[] }[];
  max_bytes: Record<string, number>;
}

export interface IntakeReport {
  // onnx
  inputs?: { name: string; dtype: string; shape: (number | string)[] }[];
  outputs?: { name: string; dtype: string; shape: (number | string)[] }[];
  opsets?: Record<string, number>;
  n_nodes?: number;
  op_histogram?: Record<string, number>;
  producer?: string;
  // device profile
  ok?: boolean;
  errors?: string[];
  warnings?: string[];
  notes?: string[];
  profile?: Record<string, unknown> | null;
  // elf
  machine?: string;
  flash_bytes?: number;
  ram_bytes?: number;
  sections?: { name: string; size: number; region: string }[];
  checks?: { name: string; used_kb: number; limit_kb: number; share: number; status: string }[];
  device?: string;
  note?: string;
  // datasheet
  scanned_pages?: number[];
  ocr?: { available: boolean; detail: string; pages_read: number[] } | null;
  candidates?: { field: string; value: number; unit: string; qualifier: string | null; page: number; line: string }[];
  // power / vendor report
  power_w_active?: number;
  power_w_idle?: number;
  tool?: string;
  fields?: Record<string, number>;
}

export interface IntakeResult {
  artifact_id: number | null;
  kind: string;
  status: "valid" | "invalid" | "needs_review" | "unsupported";
  message: string;
  required?: string;
  format?: FileFormat;
  model?: ModelProfile;
  report?: IntakeReport;
  warnings?: string[];
  spec?: Record<string, unknown>;
  project?: { spec: ProjectSpec; hardware_created: string | null; warnings: string[] };
  needs_dynamic_dim?: boolean;
  records?: Record<string, unknown>[];
}

export interface Capability {
  group: string;
  id: string;
  name: string;
  status: "available" | "needs_credential" | "unavailable" | "disabled" | "planned";
  detail: string;
  enable: string;
  location?: string;
  measures?: string[];
}

export interface PlanChange {
  id: string;
  kind: "config" | "model" | "device" | "design" | "cooling";
  tier: string;
  title: string;
  params: Record<string, unknown>;
  risk: string;
  confidence: string;
  directional: boolean;
}

export interface MetricJson { value: number; unit: string; low: number; high: number; provenance: string }

export interface ScenarioView {
  label: string;
  feasibility: string;
  bottleneck: string;
  throttled: boolean;
  checks: { name: string; status: string; limit: number; unit: string; pessimistic: number }[];
  notes: string[];
  metrics: Record<string, MetricJson>;
  parameters: Record<string, string | number>;
  config: Config;
}

export interface DiffRows {
  parameters: { name: string; unit: string; baseline: string | number; plan: string | number; changed: boolean }[];
  metrics: { name: string; unit: string; baseline: MetricJson; plan: MetricJson; change_percent: number | null; better: boolean | null; provenance_changed: boolean }[];
}

export interface PlanAnalysis {
  source_run_id: number;
  project_version: number;
  changes: PlanChange[];
  baseline: ScenarioView;
  singles: { change: PlanChange; view: ScenarioView; notes: string[]; diff: DiffRows }[];
  plan: ScenarioView;
  diff: DiffRows;
  applied: { model_id: string; device_id: string; config: Config; hardware_changes: Record<string, unknown>; notes: string[]; applied: string[]; skipped: string[] };
  accuracy: string;
  risks: { change: string; risk: string; confidence: string }[];
  directional: string[];
  summary: Record<string, unknown>;
}

export interface SavedPlan {
  id: number;
  name: string;
  version: number;
  note: string;
  changes: { id: string; title: string; kind: string }[];
  summary: Record<string, { baseline: number; plan: number; change_percent: number | null } | string>;
  project_version: number | null;
  created_at: string;
  analysis?: PlanAnalysis;
}

export interface ExportCatalogItem {
  kind: string;
  name: string;
  label: "generated_validated" | "generated" | "template";
  label_text: string;
  needs_plan: boolean;
  needs_upload?: boolean;
  available: boolean;
  reason: string | null;
}

export interface ExportItem {
  id: number;
  kind: string;
  filename: string;
  label: string;
  label_text: string;
  size_bytes: number;
  sha256: string;
  plan_id: number | null;
  validation: Record<string, unknown>;
  created_at: string;
}

export interface CoolingRow {
  id: string;
  change_id: string | null;
  name: string;
  type: string;
  r_th_k_per_w: number;
  active_w: number;
  temperature_c: number;
  unthrottled_c: number;
  throttled: boolean;
  clock_factor: number;
  power_w: number;
  energy_mj: number;
  feasibility: string;
  meets_requirements: boolean;
  operational_kgco2e_per_year: number;
  embodied_kgco2e_per_year: number;
  lifetime_kgco2e_per_year: number;
  electricity_kwh_per_year: number;
  cost_usd: { low: number; high: number } | null;
  noise_dba: number | null;
  materials: string;
  recyclability: string | null;
  maintainability: string;
  lifetime_years: number | null;
  confidence: string;
  source: string;
  r_th_range: { low: number; typical: number; high: number } | null;
  modifier: boolean;
  rating: string;
  explanation: string;
  // catalogue v2
  targets?: string[];
  heat_share_reached?: number | null;
  sustainability?: { repairability: string | null; recyclability: string | null; hazardous: string | null; water_l_per_year: number | null; water_note: string; embodied_note: string };
  cautions?: string[];
  needs?: string[];
}

export interface Environment {
  sealed_enclosure: boolean;
  dusty: boolean;
  max_noise_dba: number | null;
  max_height_mm: number | null;
  datacenter: boolean;
  facility_water: boolean;
}

export interface CoolingReport {
  catalogue_version?: number;
  criteria: string[];
  rows: CoolingRow[];
  assumptions: string[];
  excluded?: { id: string; name: string; type: string; reasons: string[] }[];
  environment?: Environment;
  heat_sources?: { source: string; name: string; heat_w: number; share: number; why: string; reached_by: string[]; label: string }[];
}
