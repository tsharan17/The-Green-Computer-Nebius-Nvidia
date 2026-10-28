# Assumptions (L0 simulator, engine 0.3.0, Green Agent 0.1)

Every constant below is an assumption until calibrated against measurements (Day 3). The UI labels these values "assumed" or "analytical"; none is shown as measured. Constants live in `backend/app/engine/assumptions.py`.

## Latency and memory (`engine/roofline.py`)

Per layer: `t_i = max(t_c,i, t_m,i) + KAPPA * min(t_c,i, t_m,i)`; total `t = sum(t_i) + n_layers * tau`. Models without a parsed graph are one aggregate layer.

| Constant | Value | Meaning |
|---|---|---|
| `ETA_COMPUTE` | fp32 0.45, fp16 0.35, int8 0.25 | Fraction of peak throughput achieved |
| `ETA_MEMORY` | 0.70 | Fraction of peak bandwidth achieved |
| `KAPPA` | 0.15 | Imperfect compute/memory overlap |
| `LAUNCH_OVERHEAD_S` | 10 µs per layer | Kernel launch overhead |
| `LIVE_ACT_FRACTION` | 0.30 | Share of activation traffic resident at once |
| `RUNTIME_OVERHEAD_GB` | 0.35 | Fixed runtime footprint |
| `USABLE_MEM_FRACTION` | 0.85 | Device memory available to the model |

Input resolution scales FLOPs and activation traffic by `scale²` (vision models only).

**Latency for streams.** For a real-time stream (`workload.streaming`, default on) frames arrive one at a time, so a batch of B makes the first frame wait for B−1 more: reported latency = batch time + (B−1)/fps, and the latency limit applies to that worst frame. Throughput and power are unaffected. Turn streaming off for offline/queued workloads. If no latency limit is set, a recommendation whose worst-case latency exceeds two frame periods carries a warning.

## ONNX parsing (`engine/workload/onnx_parser.py`)

- Graphs are analysed at batch 1. Dynamic dimensions require an explicit default (the batch axis defaults to 1).
- Conv / ConvTranspose / Gemm / MatMul MACs come from inferred tensor shapes; pooling, softmax and normalisation use rough per-element FLOP factors; data-movement ops have zero FLOPs.
- **Elementwise ops (activations, Add/Mul, BatchNorm) are assumed fused** into their producer: their FLOPs are added to the previous layer, with no extra activation traffic or kernel launch. View ops (Reshape, Flatten, ...) cost nothing.
- Every other op reads its activation inputs and writes its outputs once (no cross-layer fusion), which can overstate memory traffic for fused runtimes such as TensorRT.
- Only the ONNX protobuf format is accepted. Pickle/torch archives, files with external data and files over 100 MB are refused. LLM prefill/decode and safetensors/Hugging Face config input are not implemented yet.
- Custom models have no published accuracy; accuracy is relative-only unless the user supplies a baseline.

## Power and thermal (`engine/power.py`, `thermal.py`, `solver.py`)

- Power constants (`idle_w`, `dyn_w_max`, `mem_w_max`, `leak_w_25c`, `gamma = 2.5`) are tuned per device so full-load power at 25 °C is within 10% of the nominal power-mode cap (enforced by a test). They are not datasheet values.
- Leakage grows with temperature: `P_leak(T) = P_leak(25 °C) · exp(0.015 · (T − 25))`.
- **Coupled solve:** for each clock multiplier θ the temperature is the damped fixed point of `T = T_amb + P_avg(T) · R_th`. If the unconstrained state exceeds the throttle point, or leakage runs away, the governor bisects θ in [0.1, 1] for the highest clock that holds the throttle temperature. If even θ = 0.1 cannot, the configuration is flagged not thermally sustainable.
- Throttle at 90 °C on Jetson, 85 °C on the reference GPUs. Thermal resistances and capacitances per cooling option are assumptions.
- The warm-up curve uses the lumped RC model at the unthrottled steady power; leakage feedback during the transient is ignored.
- Unthrottled temperatures are displayed clamped at 200 °C.

## Calibration and measurements (`engine/calibration.py`)

- A measurement is a real run of (device, model, precision, batch). Fields a tool cannot supply stay empty; nothing is filled with a guess.
- For one (device, precision) two scalars are fitted: `eta_scale` (multiplies achieved compute and memory efficiency, chosen per point so predicted batch latency matches, then combined by a geometric mean shrunk toward 1 by `n/(n+1)`) and `power_scale` (only from points carrying a power measurement). There is deliberately no upper cap on the calibrated efficiency: a factor above 1 means the profile's peak or bandwidth is conservative for that device.
- **Acceptance gate:** each point is predicted from a fit to all the other points (leave-one-out). The fit is accepted only if the mean absolute relative error falls to 90% or less of the uncalibrated error, with at least 3 points. Otherwise the factors stay 1.0 and the stored report says why. Every refit is stored, accepted or not; only accepted versions are applied.
- Calibrated bands re-centre on the fitted factors and take their half-width from the largest leave-one-out error ×1.15 (3% floor, 60% ceiling). That width is derived from the same points, so coverage on new data is unproven until more measurements arrive.
- Calibration applies only to the device and precision measured. Latency-derived metrics become `calibrated` only when latency was calibrated; power-derived metrics only when power was.
- An exact match for the evaluated operating point (same device, model, precision, batch, resolution, power mode, native clocks) replaces batch time, capacity and, if measured, active power, labelled `measured`. The coupled thermal solve is not re-run on measured values, and average power at the workload's duty cycle stays predicted.
- A measured accuracy retention (vs the model's FP32 result on the same data) lets that precision be certified instead of capped at uncertain, and a measured value below the limit fails the check.
- Jobs: launching Nebius Serverless Jobs is **not implemented** (no credentials or confirmed API). The app reports this; measurements arrive by import (bench JSON, nvidia-smi CSV, tegrastats) or manual entry.

## Uncertainty

Bands (before calibration) are **corner envelopes, not statistical intervals**. The whole coupled model is re-solved at an optimistic and a pessimistic corner and each metric's band is the min/max over {optimistic, nominal, pessimistic}:

| Parameter | Corner width |
|---|---|
| Achieved efficiency (compute and memory) | ±25% |
| Every power term | ±15% |
| Thermal resistance | ±20% |

Manual-parameter models widen these by 1.5×. Whether the envelope actually covers measured values is checked against measurements on the Verify screen.

## Accuracy

- `min_accuracy` defaults to 0.90 and is interpreted as **retention relative to the model's own baseline** (`accuracy_mode: relative`). The plan's "≥ 90% mAP" cannot be met in absolute terms by any published YOLOv8 variant (mAP50-95 is 0.37–0.50), so an `absolute` mode is also supported and correctly rejects those models. Which interpretation is the product default is the owner's decision.
- Retention priors (value, low, high): fp32 1.000; fp16 0.998 (0.990–1.000); int8 0.985 (0.930–1.000).
- Resolution prior: retention × `1 − 0.30·(1 − s)` (low bound slope 0.60).
- An unmeasured int8 or reduced-resolution configuration is capped at **uncertain** even if its band clears the limit. It becomes feasible only when a measured retention is supplied (at native resolution).

## Search and ranking (`engine/optimise.py`)

- Knobs: precision (3) × batch {1,2,4,8,16} × power mode × cooling × resolution {100,75,50%} × clock trim {100,85,70%}, about 1,000–1,600 candidates per device.
- Ranking: certified feasible before uncertain, then lowest energy per inference. Energies within about 3% are treated as ties and broken by lower latency.
- Pareto set: non-dominated over energy and latency (minimised) and accuracy (maximised), among feasible and uncertain candidates only. Memory is a hard constraint, not a trade-off axis.
- Co-design comparison holds the other side at the baseline. Interaction factor `I = E_comb · E_base / (E_sw · E_hw)`; below 1 the gains reinforce, above 1 they overlap.

## Device and model library

- Model params, FLOPs and baseline accuracy are published figures. Activation traffic per library model is an estimate; ONNX uploads compute it from the graph.
- Device memory size, bandwidth and power-mode caps are vendor spec. Dense peak throughput is derived from vendor sparse TOPS (halved, GPU share assumed) and is marked assumed.
- The two reference GPUs (L40S, H100) exist to be measured on Nebius; no GPU has been measured yet and their availability there is unconfirmed.
- `host-cpu` (this development machine) is the only measured device: compute and bandwidth roofs come from `bench/probe_host.py`; power and thermal constants are assumptions and there is no power measurement.
- ResNet-50 and MobileNetV2 activation traffic comes from parsing the reference graphs in `bench/reference_models.py`, whose parameter and FLOP counts reproduce the published figures (tested).

## Broader hardware (hardware builders, engine 0.3)

- A profile may override engine defaults: `runtime_overhead_gb`, `usable_mem_fraction`, `eta_compute`, `eta_memory`, `launch_overhead_s`. Without overrides the Jetson-era defaults apply.
- `weights_mem_gb` (flash / model memory): weights are checked against it and `mem_gb` becomes the RAM that holds the largest layer's activations (without a parsed graph, 3x the average layer is assumed). Both must fit.
- `onchip_weights_mb`: when the whole model's weights fit in 85% of it, weight traffic is removed from the memory roof (FPGA BRAM/URAM, Hailo/Coral SRAM). `onchip_bw_gbs` then applies to activation traffic and `mem_bw_gbs` to anything that spills. Hailo and the Pi+Hailo kit assume weights are always resident (the compiler's multi-context scheme is not modelled), so large models are optimistic there.
- Builders (`engine/hwbuild.py`): MCU peak = MHz x int8 MACs/cycle x 2 at 15% achieved (depthwise-heavy nets on CMSIS-NN-class kernels), SRAM bandwidth 4 bytes/cycle, per-layer overhead 5,000 cycles; FPGA peak = DSPs x usable fraction (0.7) x MACs/DSP x 2 x MHz at 45% achieved; generic power split 65% dynamic / 25% memory / 10% leakage of the non-idle power; clock steps and cooling are generated and labelled assumed. A stated thermal resistance (theta-JA) is used as-is.
- Library entries added for Raspberry Pi 5 (+Hailo-8L), Hailo-8, Coral Edge TPU, L4, T4, Apple M4, Snapdragon X Elite, QCS6490, Intel Core Ultra 7 258V (NPU 4), AMD Ryzen AI 9 HX 370, STM32H743, nRF52840, ESP32-S3, STM32N6, Kria KV260. Vendor TOPS are taken as int8 and dense (sparse figures halved); marketing NPU numbers are not measurements; several memory-bandwidth and power values are estimates, flagged per entry. None has been measured.
- The MLPerf Tiny models carry approximate published parameter and MAC counts and hand-estimated activation traffic.
- Vendor report parsers (`engine/vendor_reports.py`) were written from each tool's documented layout and tested on synthetic samples, not a corpus of real reports.
- Layer view: times come from the same roofline; int8 risk is a heuristic prior by operator type and position.

## Green Agent (`backend/app/agent/`)

- The agent reasons and explains; every metric comes from a tool, which calls the deterministic engine. Tool outputs are rounded to four significant digits so they can be quoted exactly.
- **Guardrail:** every figure in a reply must equal a number in a tool result from the same turn (or one the user typed), rounded to the figure's own precision, optionally scaled by 100 or 1000 (fraction/percent, ms/s). There is no relative tolerance, because one is enough to let "99.5" pass when "100" exists. Whole numbers up to 12 are tolerated only when no unit follows. A failing reply is rewritten up to twice; if it still fails the figures are replaced by "[unverified figure]" and flagged.
- **Constraints are read-only:** the tool schema has no constraint fields and the implementation rejects attempts, so a prompt injection cannot loosen the 55 W / accuracy limits.
- At most 12 tool calls per turn (configurable); after that the model must answer without tools. Every turn and its events are stored (`agent_runs`).
- Model tiers: Ultra for planning and the final narrative, Super/Nano as fallbacks; IDs are resolved against the provider's model list. Defaults are unverified (see docs/connecting-nebius.md).
- Not implemented: Tavily datasheet import, the multimodal (Omni) datasheet reader, recipe generation, constraint repair.

## Nebius Serverless Jobs (`backend/app/integrations/jobs.py`)

Command construction follows the documented `nebius ai job create` flags and is unit tested against a fake runner. Never run against a real account. The CUDA image tag and the `onnxruntime-gpu` install inside the job are unverified; job state and id parsing search the CLI's JSON flexibly because the field names are undocumented. Jobs build only the reference models (ResNet-50, MobileNetV2) in FP32/FP16.

## Advice, repair and what-ifs (Day 5)

- **Ranking now weighs effort.** Each unit of engineering effort (precision 2-4, batch 1, resolution 3, pruning 6, power mode 1, clock 1, cooling 4) costs 1.2% of energy in the ranking, so a physical change such as a larger heatsink must earn its place. Effort scores are rough relative judgements, not measured costs.
- **Repair** returns the cheapest certified change sets (pessimistic bounds pass every requirement), ordered by effort then energy. If only uncertain options exist they are labelled as needing measurement; if none exist it names the constraints that fail in at least 90% of candidates and never relaxes them.
- **Structured pruning** (0 or 40%): compute and weights scale with the kept fraction, activations at half that rate. Accuracy prior: retention 1 - 0.15*sparsity (low bound 1 - 0.40*sparsity), no fine-tuning assumed in the prior; an unmeasured pruned model is capped at uncertain.
- **Model swaps** compare published accuracy (for example 37.3% vs 44.9% mAP) as a ratio against the original model, then apply the usual precision priors. Only models with the same task and less compute are recommended as "lighter"; their energy is compared with your recommended setup, not your baseline.
- **Hardware alternatives** are re-optimised on each other library board with a small search (precision x batch x power mode x clock). Boards flagged `alternative_candidate: false` (this computer, reference GPUs) are not suggested.
- **What-ifs** change one parameter at a time (memory bandwidth +25%, compute +25% at the precision in use, dynamic power -20%, memory power -30%, idle power -40%, leakage -50%, thermal resistance -30%) on the recommended configuration, analytical to analytical (no calibration). Elasticity is `ln(new/old) / ln(factor)`. The model has no SRAM hierarchy, dataflow or interconnect, so chip-level conclusions are directional; proving one needs architecture-level modelling, synthesis and silicon.
- **Limiting factor** is decided from the current setup: heat if it throttles; otherwise the larger latency elasticity of bandwidth vs compute (needs |e| > 0.25); then idle power (idle share of power above 50%); otherwise fixed per-layer overheads.
- **Confidence labels:** high = measured or calibrated; medium = physics prediction; low = unmeasured accuracy effect, other hardware without measurements, or any design what-if.
- **Recipes** are rendered from templates, syntax-checked only, never run on a device. Jetson power-mode ids and clock-cap mechanisms differ per board, so the scripts tell you what to look up instead of guessing.
- **Datasheet import:** extracted values must come from a page the search actually returned, be plausible (ranges enforced) and be confirmed by a person. Power model constants (idle 12%, dynamic 55%, memory 20%, leakage 6% of the top power mode) and cooling (passive sized to 70% of the thermal headroom at full power, fan 0.6x) are assumptions labelled as such.
