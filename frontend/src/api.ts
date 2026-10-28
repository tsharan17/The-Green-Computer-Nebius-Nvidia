import type {
  HardwareDraft,
  LayerProfile,
  ReportParse,
  RecipeOut,
  AgentEvent,
  AgentStatus,
  BenchmarkJob,
  BenchmarkStatus,
  CalibrationHistoryItem,
  Measurement,
  VerifyOut,
  EvaluationResult,
  HardwareProfile,
  ModelProfile,
  OptimisationResult,
  ProjectOut,
  ProjectSpec,
  UploadOut,
  ArtifactItem,
  AuditItem,
  AuthConfig,
  Me,
  RunItem,
  SessionItem,
  UsageSummary,
  VersionItem,
  Workspace,
  Capability,
  FormatCatalogue,
  IntakeResult,
} from "./types";
import type { AiCandidates, LegalDocInfo, Pipeline, RecommendationSet, TermsStatus, VerificationReport } from "./workflow/types";

const BASE = "/api/v1";

/** The workspace every request acts in (sent as X-GC-Workspace). */
let workspaceId: number | null = null;
export function setWorkspace(id: number | null) {
  workspaceId = id;
}

/** Headers every call sends: the CSRF marker the server requires for cookie sessions, and the
 * active workspace. */
export function baseHeaders(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  h.set("X-GC-CSRF", "1");
  if (workspaceId !== null) h.set("X-GC-Workspace", String(workspaceId));
  return h;
}

/** Thrown for any non-2xx answer; `status` lets callers react to 401/403/404. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const headers = baseHeaders(init?.headers);
  if (!headers.has("Content-Type") && init?.body !== undefined) headers.set("Content-Type", "application/json");
  try {
    res = await fetch(BASE + path, { ...init, headers, credentials: "same-origin" });
  } catch {
    // fetch only throws when the server cannot be reached at all.
    throw new Error(
      "Cannot reach the Green Computer server. It is probably not running (or was stopped). " +
        "Start it with scripts/dev.ps1, or run: cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000, then reload this page.",
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("gc:unauthorized"));
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** Fetch a file the server generates and hand it to the browser as a download. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const r = await fetch(BASE + path, { headers: baseHeaders(), credentials: "same-origin" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : r.statusText);
  }
  const cd = r.headers.get("Content-Disposition") || "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] || fallbackName;
  // A generic type makes every browser save the file (Edge otherwise also opens PDFs in a tab).
  const url = URL.createObjectURL(new Blob([await r.arrayBuffer()], { type: "application/octet-stream" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** Save data the page already has (chart data, tables) as a file. */
export function saveText(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const auth = {
  config: () => req<AuthConfig>("/auth/config"),
  me: () => req<Me>("/auth/me"),
  signup: (email: string, password: string, display_name: string, accept_terms: boolean) =>
    req<Me>("/auth/signup", post({ email, password, display_name, accept_terms })),
  acceptTerms: () => req<TermsStatus>("/auth/terms/accept", post()),
  legal: () => req<LegalDocInfo[]>("/auth/legal"),
  legalDoc: (doc: string) => req<LegalDocInfo & { markdown: string }>(`/auth/legal/${encodeURIComponent(doc)}`),
  signin: (email: string, password: string) => req<Me | { mfa_required: true; mfa_token: string }>("/auth/signin", post({ email, password })),
  signinMfa: (mfa_token: string, code: string) => req<Me>("/auth/signin/mfa", post({ mfa_token, code })),
  verifyEmail: (token: string) => req<{ verified: boolean; email: string }>("/auth/email/verify", post({ token })),
  resendVerification: () => req<{ sent: boolean; message: string; dev_verify_url?: string }>("/auth/email/resend", post()),
  mfaSetup: () => req<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", post()),
  mfaEnable: (code: string) => req<{ enabled: boolean; recovery_codes: string[]; note: string }>("/auth/mfa/enable", post({ code })),
  mfaDisable: (password: string, code: string) => req<{ enabled: boolean }>("/auth/mfa/disable", post({ password, code })),
  signout: () => req<{ signed_out: boolean }>("/auth/signout", post()),
  forgot: (email: string) =>
    req<{ sent: boolean; message: string; dev_reset_url?: string; dev_note?: string }>("/auth/password/forgot", post({ email })),
  reset: (token: string, password: string) => req<{ reset: boolean }>("/auth/password/reset", post({ token, password })),
  change: (current_password: string, new_password: string) =>
    req<{ changed: boolean; other_sessions_signed_out: number }>("/auth/password/change", post({ current_password, new_password })),
  sessions: () => req<SessionItem[]>("/auth/sessions"),
  revoke: (id: number) => req<{ revoked: boolean }>(`/auth/sessions/${id}/revoke`, post()),
  token: (label: string, days: number) =>
    req<{ id: number; token: string; expires_at: string; note: string }>("/auth/tokens", post({ label, days })),
  deleteAccount: (password: string, confirm: string) =>
    req<{ deleted: boolean; workspaces_deleted: number; projects_deleted: number; files_deleted: number }>(
      "/auth/account/delete",
      post({ password, confirm }),
    ),
};

export const plans = {
  options: (pid: number) => req<{ source_run_id: number; recommended: string[]; changes: import("./types").PlanChange[] }>(`/projects/${pid}/plan-options`),
  analyse: (pid: number, change_ids: string[]) => req<import("./types").PlanAnalysis>(`/projects/${pid}/plans/analyse`, post({ change_ids })),
  /** The predicted trace of today's setup with these changes applied (model, hardware, cooling and settings). */
  trace: (pid: number, change_ids: string[]) => req<import("./twin/types").StoredTrace>(`/projects/${pid}/plans/trace`, post({ change_ids })),
  save: (pid: number, change_ids: string[], name: string, note: string) =>
    req<import("./types").SavedPlan>(`/projects/${pid}/plans`, post({ change_ids, name, note })),
  list: (pid: number) => req<import("./types").SavedPlan[]>(`/projects/${pid}/plans`),
  get: (pid: number, id: number) => req<import("./types").SavedPlan>(`/projects/${pid}/plans/${id}`),
  cooling: (pid: number, which: "baseline" | "best") =>
    req<{ criteria: string[]; rows: import("./types").CoolingRow[]; assumptions: string[] }>(`/projects/${pid}/cooling?which=${which}`),
  exportCatalog: (pid: number) => req<import("./types").ExportCatalogItem[]>(`/projects/${pid}/exports/catalog`),
  createExport: (pid: number, kind: string, plan_id: number | null) => req<import("./types").ExportItem>(`/projects/${pid}/exports`, post({ kind, plan_id })),
  exports: (pid: number) => req<import("./types").ExportItem[]>(`/projects/${pid}/exports`),
};

/** A server-drawn PNG as a data URL (the content security policy allows `self` and `data:` images). */
async function pngDataUrl(path: string): Promise<string> {
  const r = await fetch(BASE + path, { headers: baseHeaders(), credentials: "same-origin" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : r.statusText);
  }
  const blob = await r.blob();
  return await new Promise<string>((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(new Error("could not read the figure"));
    fr.readAsDataURL(blob);
  });
}

export const workflow = {
  pipeline: (pid: number) => req<Pipeline>(`/projects/${pid}/pipeline`),
  verification: (pid: number) => req<VerificationReport>(`/projects/${pid}/verification`),
  recommendations: (pid: number) => req<RecommendationSet>(`/projects/${pid}/recommendations`),
  aiCandidates: (pid: number, refresh = false) => req<AiCandidates>(`/projects/${pid}/recommendations/ai`, post({ refresh })),
  figure: (pid: number, name: string) => pngDataUrl(`/projects/${pid}/figures/${name}?theme=light`),
};

export const twin = {
  create: (pid: number, which: "baseline" | "best" | "custom", config?: import("./types").Config) =>
    req<import("./twin/types").StoredTrace>(`/projects/${pid}/traces?which=${which}`, post(config)),
  /** A matplotlib/seaborn figure of a stored trace, as a data URL. */
  figure: (pid: number, id: number, name: string, theme: "light" | "dark"): Promise<string> =>
    pngDataUrl(`/projects/${pid}/traces/${id}/figures/${name}?theme=${theme}`),
  get: (pid: number, id: number) => req<import("./twin/types").StoredTrace>(`/projects/${pid}/traces/${id}`),
};

export const intake = {
  formats: () => req<FormatCatalogue>("/intake/formats"),
  capabilities: () => req<Capability[]>("/capabilities"),
  upload: async (file: File, opts: { category?: string; projectId?: number; dynamicDim?: number; supplyV?: number }) => {
    const q = new URLSearchParams({ filename: file.name });
    if (opts.category) q.set("category", opts.category);
    if (opts.projectId) q.set("project_id", String(opts.projectId));
    if (opts.dynamicDim) q.set("dynamic_dim_default", String(opts.dynamicDim));
    if (opts.supplyV) q.set("supply_v", String(opts.supplyV));
    return req<IntakeResult>(`/intake/upload?${q}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: await file.arrayBuffer(),
    });
  },
};

export const workspace = {
  list: () => req<Workspace[]>("/workspaces"),
  create: (name: string) => req<Workspace>("/workspaces", post({ name })),
  members: () => req<{ user_id: number; email: string; display_name: string; role: string }[]>("/workspaces/current/members"),
  addMember: (email: string, role: string) => req<unknown>("/workspaces/current/members", post({ email, role })),
  removeMember: (userId: number) => req<unknown>(`/workspaces/current/members/${userId}`, { method: "DELETE" }),
  usage: () => req<UsageSummary>("/usage"),
  audit: (projectId?: number) => req<AuditItem[]>(`/audit${projectId ? `?project_id=${projectId}` : ""}`),
  artifacts: (projectId?: number) => req<ArtifactItem[]>(`/artifacts${projectId ? `?project_id=${projectId}` : ""}`),
  deleteArtifact: (id: number) => req<unknown>(`/artifacts/${id}`, { method: "DELETE" }),
};

export const history = {
  versions: (pid: number) => req<VersionItem[]>(`/projects/${pid}/versions`),
  restore: (pid: number, version: number) => req<ProjectOut>(`/projects/${pid}/versions/${version}/restore`, post()),
  runs: (pid: number) => req<RunItem[]>(`/projects/${pid}/runs`),
  run: <T>(pid: number, runId: number) => req<{ id: number; kind: string; created_at: string; result: T }>(`/projects/${pid}/runs/${runId}`),
  archive: (pid: number, archived: boolean) => req<ProjectOut>(`/projects/${pid}/archive`, post({ archived })),
  projects: (includeArchived = false) => req<ProjectOut[]>(`/projects${includeArchived ? "?include_archived=true" : ""}`),
};

export const api = {
  models: () => req<ModelProfile[]>("/models/library"),
  devices: () => req<HardwareProfile[]>("/hardware/library"),
  projects: () => req<ProjectOut[]>("/projects"),
  project: (id: number) => req<ProjectOut>(`/projects/${id}`),
  save: (id: number, spec: ProjectSpec) =>
    req<ProjectOut>(`/projects/${id}`, { method: "PUT", body: JSON.stringify(spec) }),
  create: (spec: ProjectSpec) =>
    req<ProjectOut>("/projects", { method: "POST", body: JSON.stringify(spec) }),
  uploadModel: async (file: File, opts: { dynamicDim?: number; baselineAccuracy?: number }) => {
    const q = new URLSearchParams({ filename: file.name, name: file.name.replace(/\.[^.]+$/, "") });
    if (opts.dynamicDim) q.set("dynamic_dim_default", String(opts.dynamicDim));
    if (opts.baselineAccuracy) q.set("baseline_accuracy", String(opts.baselineAccuracy));
    return req<UploadOut>(`/models/upload?${q}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: await file.arrayBuffer(),
    });
  },
  manualModel: (body: { name: string; params_m: number; gflops: number; n_layers: number }) =>
    req<ModelProfile>("/models/manual", { method: "POST", body: JSON.stringify(body) }),
  verify: (deviceId?: string) => req<VerifyOut>(`/verify${deviceId ? `?device_id=${deviceId}` : ""}`),
  calibration: () =>
    req<{ active: Record<string, unknown>; history: CalibrationHistoryItem[] }>("/calibration"),
  benchmarkStatus: () => req<BenchmarkStatus>("/benchmarks/status"),
  addMeasurement: (body: Record<string, unknown>) =>
    req<{ created: number; measurements: Measurement[] }>("/measurements", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  importMeasurements: (format: string, text: string, params: Record<string, string>) =>
    req<{ created: number; skipped_duplicates: number }>(
      `/measurements/import?${new URLSearchParams({ format, ...params })}`,
      { method: "POST", headers: { "Content-Type": "text/plain" }, body: text },
    ),
  agentStatus: () => req<AgentStatus>("/agent/status"),
  benchmarks: () => req<BenchmarkJob[]>("/benchmarks"),
  refreshBenchmark: (id: number) => req<BenchmarkJob>(`/benchmarks/${id}`),
  submitBenchmark: (body: { device_id: string; model_id: string; precision: string; batches: number[] }) =>
    req<BenchmarkJob>("/benchmarks", { method: "POST", body: JSON.stringify(body) }),
  recipe: (id: number) => req<RecipeOut>(`/projects/${id}/recipe`),
  reportText: async (id: number, format: "md" | "html") => {
    const r = await fetch(`${BASE}/projects/${id}/report?format=${format}`, { headers: baseHeaders(), credentials: "same-origin" });
    if (!r.ok) throw new Error(`${r.status}: ${(await r.json().catch(() => ({ detail: r.statusText }))).detail}`);
    return r.text();
  },
  drafts: () => req<HardwareDraft[]>("/hardware/drafts"),
  importHardware: (name: string) =>
    req<HardwareDraft>("/hardware/import", { method: "POST", body: JSON.stringify({ name }) }),
  manualHardware: (body: Record<string, unknown>) =>
    req<HardwareDraft>("/hardware/manual", { method: "POST", body: JSON.stringify(body) }),
  confirmDraft: (id: number, overrides: Record<string, unknown>) =>
    req<HardwareProfile>(`/hardware/drafts/${id}/confirm`, { method: "POST", body: JSON.stringify({ overrides }) }),
  rejectDraft: (id: number) => req<HardwareDraft>(`/hardware/drafts/${id}/reject`, { method: "POST" }),
  customPreview: (body: Record<string, unknown>) =>
    req<HardwareProfile>("/hardware/custom/preview", { method: "POST", body: JSON.stringify(body) }),
  customCreate: (body: Record<string, unknown>) =>
    req<HardwareProfile>("/hardware/custom", { method: "POST", body: JSON.stringify(body) }),
  parseReport: (text: string, tool?: string, supplyV?: number) =>
    req<ReportParse>("/hardware/parse-report", {
      method: "POST",
      body: JSON.stringify({ text, ...(tool ? { tool } : {}), ...(supplyV ? { supply_v: supplyV } : {}) }),
    }),
  layers: (id: number, config?: unknown) =>
    req<LayerProfile>(`/projects/${id}/layers`, { method: "POST", body: config ? JSON.stringify(config) : undefined }),
  inspectFile: async (file: File) =>
    req<Record<string, unknown> & { kind: string }>(`/files/inspect?${new URLSearchParams({ filename: file.name })}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: await file.arrayBuffer(),
    }),
  registerProfile: (profile: HardwareProfile) =>
    req<HardwareProfile>("/hardware/profile", { method: "POST", body: JSON.stringify(profile) }),
  evaluate: (id: number) => req<EvaluationResult>(`/projects/${id}/evaluate`, { method: "POST" }),
  optimise: (id: number) => req<OptimisationResult>(`/projects/${id}/optimise`, { method: "POST" }),
};

/** POST a chat turn and read the server-sent events as they arrive. */
export async function agentChat(
  body: { project_id: number; message: string; history: { role: string; content: string }[] },
  onEvent: (e: AgentEvent) => void,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(BASE + "/agent/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: baseHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Cannot reach the Green Computer server. Is it running? Start it with scripts/dev.ps1.");
  }
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as AgentEvent);
      }
    }
  }
}

export interface ExecutionJob {
  id: number;
  backend: string;
  status: "queued" | "running" | "succeeded" | "failed";
  params: Record<string, unknown>;
  result: {
    latency_ms?: { mean: number; p50: number; p95: number; p99: number; min: number; max: number };
    throughput_fps?: number;
    runs?: number;
    providers?: string[];
    onnxruntime?: string;
    peak_rss_bytes?: number | null;
    inputs?: Record<string, number[]>;
    precision?: string;
    operators?: number;
    notes?: string[];
    hardware_label?: string;
    comparison?: {
      predicted_batch_ms?: number;
      measured_batch_ms?: number;
      measured_p95_ms?: number;
      error_percent?: number | null;
      by_op?: { op: string; predicted_ms: number; measured_ms: number; error_percent: number | null }[];
      note?: string;
      measurement_note?: string;
    };
  };
  error: string | null;
  measurement_id: number | null;
  trace_id: number | null;
  seconds: number;
  created_at: string;
  finished_at: string | null;
}

export const execution = {
  backends: () => req<import("./types").Capability[]>("/execution/backends"),
  run: (pid: number, body: { backend: string; batch: number; runs: number; warmup: number }) =>
    req<ExecutionJob>(`/projects/${pid}/executions`, post(body)),
  list: (pid: number) => req<ExecutionJob[]>(`/projects/${pid}/executions`),
  get: (pid: number, id: number) => req<ExecutionJob>(`/projects/${pid}/executions/${id}`),
};

export const prefs = {
  get: () => req<{ mode: "simple" | "engineering"; onboarded: boolean }>("/auth/preferences"),
  set: (p: { mode?: "simple" | "engineering"; onboarded?: boolean }) =>
    req<{ mode: "simple" | "engineering"; onboarded: boolean }>("/auth/preferences", { method: "PUT", body: JSON.stringify(p) }),
};

export interface UseCase {
  id: string;
  title: string;
  blurb: string;
  model_id: string;
  device_id: string;
  fps: number;
  ambient_c: number;
  max_power_w: number;
  max_latency_ms: number | null;
  priority: string;
  category: string;
}

export interface Health {
  project_id: number;
  analysed: boolean;
  stale: boolean;
  outcome?: string;
  best_outcome?: string | null;
  bottleneck?: string | null;
  headline?: string | null;
  energy_saving_percent?: number | null;
  analysed_at?: string;
}

export interface SearchHit { type: string; title: string; subtitle: string; href: string }

export const guide = {
  useCases: () => req<{ use_cases: UseCase[]; priorities: { id: string; title: string; blurb: string }[] }>("/guide/use-cases"),
  start: (body: Record<string, unknown>) =>
    req<{ project: import("./types").ProjectOut; analysis?: { outcome: string; best_outcome: string | null } }>("/guide/start", post(body)),
  demo: () => req<{ created: { project_id: number; name: string; outcome: string }[] }>("/guide/demo", post()),
  health: () => req<Health[]>("/projects-health"),
  search: (q: string) => req<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
};

// --- hardware system graph ---------------------------------------------------------------------

export type GraphConfidence = "stated" | "parsed" | "derived" | "assumed" | "measured";
export interface GraphAttr { value: number | string | boolean | null; unit: string | null; origin: string; confidence: GraphConfidence; artifact_id: number | null; note: string }
export interface GraphComponent {
  id: string; name: string; kind: string; parent: string | null; attributes: Record<string, GraphAttr>;
  origin: string; confidence: GraphConfidence; artifact_id: number | null; trace_component: string | null;
  logical: { x: number; y: number; w: number; h: number; z: number } | null;
  physical: { x_mm: number; y_mm: number; rotation_deg: number; side: "top" | "bottom"; width_mm: number | null; height_mm: number | null } | null;
}
export interface GraphConnection { id: string; source: string; target: string; kind: string; label: string; attributes: Record<string, GraphAttr>; origin: string; confidence: GraphConfidence; artifact_id: number | null }
export interface SystemGraph {
  schema_id: string; device_id: string; device_name: string; components: GraphComponent[]; connections: GraphConnection[];
  sources: { origin: string; artifact_id: number | null; filename: string | null; components: number; connections: number; attributes: number }[];
  warnings: string[]; limitations: string[];
}
export interface GraphLinks { trace_id: number; kind: string; provenance: string; label: string; components: Record<string, { events: number; busy_ms: number; heat_w?: number }> }
export interface GraphState {
  version: number; saved: boolean; outdated?: boolean; note?: string; created_at?: string; source_artifacts?: number[];
  graph: SystemGraph; links: GraphLinks | null;
}
export interface GraphVersion { id: number; version: number; note: string; created_at: string; components: number; connections: number; source_artifacts: number[] }
export interface GraphSourceFile { id: number; filename: string; tool: string; kind: string; project_id: number | null; created_at: string }
export interface GraphDiff {
  from: number; to: number; components_added: string[]; components_removed: string[]; connections_added: string[]; connections_removed: string[];
  attributes_changed: { component: string; attribute: string; from: unknown; to: unknown }[];
}

export const systemGraph = {
  current: (pid: number) => req<GraphState>(`/projects/${pid}/system-graph`),
  version: (pid: number, v: number) => req<GraphState>(`/projects/${pid}/system-graph/${v}`),
  versions: (pid: number) => req<GraphVersion[]>(`/projects/${pid}/system-graph/versions`),
  sources: (pid: number) => req<GraphSourceFile[]>(`/projects/${pid}/system-graph/sources`),
  fromFiles: (pid: number, artifact_ids: number[], note = "") =>
    req<GraphVersion>(`/projects/${pid}/system-graph/from-files`, { method: "POST", body: JSON.stringify({ artifact_ids, note }) }),
  diff: (pid: number, from: number, to: number) => req<GraphDiff>(`/projects/${pid}/system-graph/diff?from=${from}&to=${to}`),
};

// --- physics views and evidence ------------------------------------------------------------------

export interface ThermalNode { id: string; name: string; temperature_c: number; heat_w: number; label: string }
export interface ThermalResistor { a: string; b: string; k_per_w: number; source: string; note: string }
export interface ThermalNodesView {
  ambient_c: number; lumped_c: number; lumped_power_w: number; heat_scale: number; nodes: ThermalNode[]; resistors: ThermalResistor[];
  hottest: string; labels: Record<string, string>; status: "lumped only" | "validated" | "not validated";
  measured: { temperature_c: number; source: string; label: string; difference_c: number; validated: boolean } | null;
}
export interface MemoryLevel {
  id: string; name: string; capacity_bytes: number | null; bandwidth_gbs: number | null; holds: string; need_bytes: number | null;
  fits: boolean | null; traffic_bytes: number | null; time_ms: number | null; source: string;
}
export interface RuntimeEffect { id: string; title: string; ms: number | null; per: string; in_headline: boolean; provenance: string; note: string }
export interface Physics {
  which: string; label: string; config: Record<string, unknown>; trace_kind: string; trace_provenance: string;
  thermal: ThermalNodesView | null; memory: MemoryLevel[];
  runtime: { effects: RuntimeEffect[]; headline_latency_ms: number; note: string;
    queue: { mode: string; utilisation: number; stable?: boolean; wait_random_ms?: number; end_to_end_ms?: number | null; seconds_per_1000_inputs?: number } };
}
export interface CalibGrade { grade: string; why: string; n?: number; error_before?: number | null; error_after?: number | null; factor?: number }
export interface CalibrationQuality {
  device_id: string; device_name: string; verified_profile: boolean; note: string;
  precisions: { precision: string; applied: boolean; version: number | null; scope: string | null; fitted_at: string | null;
    latency: CalibGrade; power: CalibGrade; measurements: number; batches: number[]; with_temperature: number }[];
}
export interface EvidenceMetric {
  key: string; value: number; low: number; high: number; unit: string; provenance: string;
  facts: { fact: string; source: string }[]; calibrated: boolean; calibration_version: number | null;
}
export interface Evidence {
  device: { id: string; name: string; verified: boolean; notes: string }; calibration: CalibrationQuality; message?: string; engine_version?: string;
  setups: { label: string; config: Record<string, unknown>; feasibility: string; metrics: EvidenceMetric[]; notes: string[] }[];
}

const physicsCache = new Map<string, Promise<Physics>>();
export const physics = {
  get: (pid: number, which: "baseline" | "best", version: number) => {
    const key = `${pid}:${which}:${version}`;
    if (!physicsCache.has(key)) {
      const p = req<Physics>(`/projects/${pid}/physics?which=${which}`);
      p.catch(() => physicsCache.delete(key));
      physicsCache.set(key, p);
    }
    return physicsCache.get(key)!;
  },
  evidence: (pid: number) => req<Evidence>(`/projects/${pid}/evidence`),
  calibration: (deviceId: string) => req<CalibrationQuality>(`/calibration/quality?device_id=${encodeURIComponent(deviceId)}`),
};

// --- remote device agents ------------------------------------------------------------------------

export interface RemoteDevice {
  id: number; name: string; device_id: string; status: "pending" | "paired" | "revoked"; online: boolean;
  last_seen_at: string | null; agent_info: Record<string, string>; key_version: number; created_at: string | null;
  pairing_expires_at: string | null; pairing_code?: string;
}
export interface QualityCheck { id: string; status: "pass" | "warn" | "fail"; detail: string }
export interface RemoteJob {
  id: number; remote_device_id: number; project_id: number; artifact_id: number;
  status: "queued" | "claimed" | "succeeded" | "failed" | "rejected" | "expired";
  params: { precision: string; batch: number; warmup: number; runs: number; max_seconds: number } | null;
  artifact: { id: number; sha256: string; size_bytes: number; filename: string } | null;
  mode: "measured" | "simulated" | null; environment: Record<string, string> | null;
  quality: { verdict: "good" | "usable" | "poor"; checks: QualityCheck[]; stats: Record<string, number> | null } | null;
  measurement_id: number | null; error: string | null; created_at: string | null; expires_at: string | null; finished_at: string | null;
}

export const remote = {
  devices: () => req<RemoteDevice[]>("/remote-devices"),
  register: (name: string, device_id: string) => req<RemoteDevice>("/remote-devices", { method: "POST", body: JSON.stringify({ name, device_id }) }),
  pairingCode: (id: number) => req<RemoteDevice>(`/remote-devices/${id}/pairing-code`, { method: "POST" }),
  revoke: (id: number) => req<RemoteDevice>(`/remote-devices/${id}`, { method: "DELETE" }),
  approve: (id: number, body: { project_id: number; artifact_id: number; precision: string; batch: number; warmup: number; runs: number; max_seconds: number }) =>
    req<RemoteJob>(`/remote-devices/${id}/jobs`, { method: "POST", body: JSON.stringify(body) }),
  jobs: (projectId: number) => req<RemoteJob[]>(`/remote-jobs?project_id=${projectId}`),
};

// --- hand-over ------------------------------------------------------------------------------------

export interface OptimisedModel { kind: string; name: string; status: string; how: string }
export interface ManifestCheck { ok: boolean; problems: string[]; compared: number }
export const handoff = {
  optimised: () => req<OptimisedModel[]>("/exports/optimised-models"),
  verify: (manifest: unknown) => req<ManifestCheck>("/reproduce/verify", { method: "POST", body: JSON.stringify(manifest) }),
};

// --- workspace data -----------------------------------------------------------------------------------

export type RetentionRules = Record<string, number | null>;
export const workspaceData = {
  retention: () => req<{ rules: RetentionRules; labels: Record<string, string>; audit_min_days: number }>("/workspaces/current/retention"),
  setRetention: (rules: RetentionRules) => req<{ rules: RetentionRules }>("/workspaces/current/retention", { method: "PUT", body: JSON.stringify(rules) }),
  apply: (dryRun: boolean) =>
    req<{ dry_run: boolean; removed: Record<string, number> }>(`/workspaces/current/retention/apply?dry_run=${dryRun}`, { method: "POST" }),
};
