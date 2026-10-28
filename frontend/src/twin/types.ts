/** Mirrors backend/app/schemas/twin.py (schema "green-computer.trace/1"). */

export interface Layout { x: number; y: number; w: number; h: number; z: number }

export interface Component {
  id: string;
  kind: "compute" | "memory" | "storage" | "link" | "power" | "cooling";
  name: string;
  detail: string;
  capacity: number | null;
  capacity_unit: string | null;
  heat_w: number;
  heat_share: number;
  provenance: string;
  layout: Layout;
}

export interface MemoryPath { id: string; source: string; target: string; label: string; bandwidth_gbs: number; peak_utilisation: number }
export interface TensorInfo { id: string; bytes: number; producer: number; last_use: number; kind: "input" | "activation" | "output" }
export interface EnergySplit { compute: number; memory: number; idle: number; leakage: number; cooling: number }

export interface TraceEvent {
  index: number;
  name: string;
  op: string;
  kind: "operator" | "aggregate" | "idle" | "queue";
  stage: number;
  start_ms: number;
  duration_ms: number;
  compute_ms: number;
  memory_ms: number;
  overhead_ms: number;
  bound: "compute" | "memory" | "overhead" | "idle";
  flops: number;
  weight_bytes: number;
  activation_bytes: number;
  bandwidth_gbs: number;
  bandwidth_utilisation: number;
  alloc: string[];
  free: string[];
  working_set_bytes: number;
  live_after_bytes: number;
  power_w: number;
  energy_mj: EnergySplit;
  components: string[];
  intensity: number;
}

export interface MemoryPoint { t_ms: number; event: number; weights_bytes: number; runtime_bytes: number; activation_bytes: number; total_bytes: number; capacity_bytes: number; over_capacity: boolean }
export interface Stage { index: number; name: string; first_event: number; last_event: number; start_ms: number; duration_ms: number; energy_mj: number; dominant_op: string }
export interface Bottleneck { kind: string; title: string; detail: string; event: number | null; component: string | null }

export interface ThermalView {
  ambient_c: number;
  steady_c: number;
  unthrottled_c: number;
  throttle_c: number;
  shutdown_c: number;
  throttled: boolean;
  clock_factor: number;
  time_to_throttle_s: number | null;
  warmup: [number, number][];
  cooling_name: string;
  r_th_k_per_w: number;
  r_th_needed_k_per_w: number | null;
  note: string;
}

export interface TraceTotals {
  latency_ms: number;
  batch_ms: number;
  period_ms: number;
  busy_fraction: number;
  energy_per_inference_mj: number;
  energy_per_batch_mj: number;
  average_power_w: number;
  active_power_w: number;
  peak_working_memory_bytes: number;
  analysis_memory_bytes: number;
  memory_capacity_bytes: number;
  weights_bytes: number;
  flops_per_batch: number;
}

export interface ExecutionTrace {
  schema_id: string;
  kind: "predicted" | "measured";
  provenance: "analytical" | "calibrated" | "estimated" | "measured" | "imported";
  source: string;
  fidelity: "graph" | "graph_approx" | "aggregate";
  fidelity_note: string;
  model_id: string;
  model_name: string;
  device_id: string;
  device_name: string;
  config: Record<string, unknown>;
  precision: string;
  batch: number;
  totals: TraceTotals;
  components: Component[];
  paths: MemoryPath[];
  tensors: TensorInfo[];
  events: TraceEvent[];
  stages: Stage[];
  memory_timeline: MemoryPoint[];
  bottlenecks: Bottleneck[];
  thermal: ThermalView;
  energy_per_inference: EnergySplit;
  assumptions: string[];
  consistency_scale: number;
  energy_consistency_scale: number;
}

export interface StoredTrace { id: number; label: string; trace: ExecutionTrace }
