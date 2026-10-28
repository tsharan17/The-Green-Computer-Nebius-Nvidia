"""Memory hierarchy and runtime effects around the predicted kernel time. Pure.

The headline latency is the engine's batch time (roofline per operator, launch overhead included).
Real deployments add effects the headline leaves out on purpose; this module lists each one with
its size, whether the headline includes it, and how it was obtained:

  analytical  computed by the engine from the trace and the profile
  assumed     depends on a default (storage speed, link speed, arrival pattern) named in the note
"""

from __future__ import annotations

from typing import Any

from app.engine import assumptions as A
from app.schemas.hardware import HardwareProfile
from app.schemas.twin import ExecutionTrace

# Assumed read speeds for loading weights at start-up (GB/s) and host links (GB/s, effective).
STORAGE_GBS = {"datacenter_gpu": 2.0, "laptop_soc": 2.0, "edge_soc": 0.25, "sbc": 0.08, "accelerator": 0.4, "fpga": 0.1, "custom": 0.25}
HOST_LINK_GBS = {"datacenter_gpu": 12.0, "accelerator": 0.4}
RUNTIME_INIT_MS = {"datacenter_gpu": 800.0, "laptop_soc": 400.0, "edge_soc": 600.0, "sbc": 300.0, "accelerator": 300.0,
                   "fpga": 2000.0, "mcu": 5.0, "custom": 500.0}  # fmt: skip


def _src(hw: HardwareProfile, key: str) -> str:
    s = (hw.sources or {}).get(key)
    return s.value if s is not None else "library profile"


def memory_hierarchy(hw: HardwareProfile, t: ExecutionTrace, mem_scale: float = 1.0) -> list[dict[str, Any]]:
    """Each memory level: capacity, bandwidth, what lives there, traffic per batch and its time."""
    tot = t.totals
    w_bytes = tot.weights_bytes
    act_traffic = sum(e.activation_bytes for e in t.events)
    w_traffic = sum(e.weight_bytes for e in t.events)
    comp_ids = {c.id for c in t.components}
    levels: list[dict[str, Any]] = []
    onchip_cap = (hw.onchip_weights_mb or 0) * 1e6
    weights_onchip = "onchip" in comp_ids and 0 < w_bytes <= onchip_cap
    if hw.onchip_weights_mb:
        bw = hw.onchip_bw_gbs
        traffic = w_traffic if weights_onchip else 0.0
        levels.append({"id": "onchip", "name": "On-chip memory", "capacity_bytes": onchip_cap, "bandwidth_gbs": bw,
                       "holds": "weights" if weights_onchip else "nothing of this model (weights do not fit)",
                       "need_bytes": w_bytes, "fits": weights_onchip, "traffic_bytes": traffic,
                       "time_ms": (traffic / (bw * 1e9) * 1e3) if bw and traffic else None,
                       "source": _src(hw, "onchip_weights_mb")})  # fmt: skip
    main_bw = hw.mem_bw_gbs * mem_scale
    main_traffic = act_traffic + (0.0 if weights_onchip or "flash" in comp_ids else w_traffic)
    usable = hw.mem_gb * 1e9 * (hw.usable_mem_fraction or 1.0)
    levels.append({"id": "ram", "name": "SRAM" if hw.kind == "mcu" else "Main memory", "capacity_bytes": usable,
                   "bandwidth_gbs": main_bw, "holds": "activations" + ("" if weights_onchip or "flash" in comp_ids else " and weights"),
                   "need_bytes": tot.peak_working_memory_bytes, "fits": tot.peak_working_memory_bytes <= usable,
                   "traffic_bytes": main_traffic, "time_ms": main_traffic / (main_bw * 1e9) * 1e3 if main_bw else None,
                   "source": _src(hw, "mem_bw_gbs")})  # fmt: skip
    if "flash" in comp_ids and hw.weights_mem_gb:
        levels.append({"id": "flash", "name": "Flash (weights)", "capacity_bytes": hw.weights_mem_gb * 1e9,
                       "bandwidth_gbs": None, "holds": "weights, read in place", "need_bytes": w_bytes,
                       "fits": w_bytes <= hw.weights_mem_gb * 1e9, "traffic_bytes": w_traffic, "time_ms": None,
                       "source": _src(hw, "mem_gb")})  # fmt: skip
    link = HOST_LINK_GBS.get(hw.kind)
    if link:
        levels.append({"id": "host", "name": "Host memory (over the link)", "capacity_bytes": None, "bandwidth_gbs": link,
                       "holds": "inputs and outputs before and after the run", "need_bytes": None, "fits": None,
                       "traffic_bytes": None, "time_ms": None, "source": f"assumed {link:g} GB/s effective link"})  # fmt: skip
    return levels


def runtime_effects(
    hw: HardwareProfile, t: ExecutionTrace, fps: float, streaming: bool, batch: int,
) -> dict[str, Any]:
    tot = t.totals
    n_kernels = sum(1 for e in t.events if e.kind in ("operator", "aggregate"))
    overhead_batch = sum(e.overhead_ms for e in t.events)
    tau_ms = (hw.launch_overhead_s if hw.launch_overhead_s is not None else A.LAUNCH_OVERHEAD_S) * 1e3
    in_bytes = sum(x.bytes for x in t.tensors if x.kind == "input")
    out_bytes = sum(x.bytes for x in t.tensors if x.kind == "output")
    effects: list[dict[str, Any]] = []

    def add(eid: str, title: str, ms: float | None, per: str, in_headline: bool, prov: str, note: str) -> None:
        effects.append({"id": eid, "title": title, "ms": None if ms is None else round(ms, 6), "per": per,
                        "in_headline": in_headline, "provenance": prov, "note": note})  # fmt: skip

    share = overhead_batch / tot.batch_ms if tot.batch_ms else 0.0
    add("launch", "Kernel launch and framework overhead", overhead_batch / batch, "inference", True, "analytical",
        f"{n_kernels} kernels × {tau_ms * 1e3:.0f} µs; {share:.0%} of the batch time.")
    add("fusion", "Operator fusion", tau_ms, "kernel removed", True, "assumed",
        f"Each operator a runtime fuses into its neighbour saves about one launch ({tau_ms * 1e3:.0f} µs). The trace "
        f"already counts {n_kernels} fused kernels (activations folded into the layer before them); "
        + ("fusing further is worth checking." if share > 0.2 else "launch overhead is small here, so more fusion gains little."))
    link = HOST_LINK_GBS.get(hw.kind)
    if link and in_bytes + out_bytes == 0:
        add("transfer", "Copying inputs and outputs over the host link", None, "inference", False, "assumed",
            "Input and output sizes are unknown for this model (no operator graph); upload the ONNX file to size the copy.")
    elif link:
        add("transfer", "Copying inputs and outputs over the host link", (in_bytes + out_bytes) / (link * 1e9) * 1e3 / batch,
            "inference", False, "assumed",
            f"{(in_bytes + out_bytes) / 1e6:.2f} MB per batch at an assumed {link:g} GB/s. Zero if inputs already "
            "live on the device or the copy overlaps compute.")  # fmt: skip
    else:
        add("transfer", "Copying inputs and outputs", 0.0, "inference", True, "analytical",
            "Shared memory: the compute engine reads inputs where they are.")
    storage = STORAGE_GBS.get(hw.kind)
    init = RUNTIME_INIT_MS.get(hw.kind, 500.0)
    load_ms = (tot.weights_bytes / (storage * 1e9) * 1e3 if storage else 0.0) + init
    add("loading", "Loading the model at start-up", load_ms, "start-up (once)", False, "assumed",
        (f"{tot.weights_bytes / 1e6:.1f} MB of weights at an assumed {storage:g} GB/s plus about {init:.0f} ms runtime set-up."
         if storage else f"Weights execute in place from flash; about {init:.0f} ms start-up assumed."))  # fmt: skip

    service_ms = tot.batch_ms  # one batch occupies the device for this long
    rho = fps * tot.batch_ms / 1000.0 / batch if batch else 0.0
    queue: dict[str, Any] = {"mode": "streaming" if streaming else "offline", "utilisation": round(rho, 4)}
    if streaming:
        fill = (batch - 1) / (2.0 * fps) * 1e3 if fps > 0 and batch > 1 else 0.0
        add("batching", "Waiting for a batch to fill", fill, "inference (average)", False, "analytical",
            f"Inputs arrive at {fps:g}/s; on average each waits for {batch - 1} more before the batch starts."
            if batch > 1 else "Batch of one: no waiting for others.")
        if rho >= 1:
            add("queueing", "Queueing", None, "inference", False, "analytical",
                f"The device is busy {rho:.0%} of the time needed: inputs pile up without bound. Lower the input rate or speed up the setup.")
            queue["stable"] = False
        else:
            wq = rho / (2.0 * (1.0 - rho)) * service_ms  # M/D/1 mean wait
            add("queueing", "Queueing behind earlier inputs", wq, "inference (average)", False, "assumed",
                f"Device busy {rho:.0%} of the time. Steady arrivals (a camera at a fixed frame rate) wait 0 ms; "
                f"random arrivals wait about {wq:.3g} ms on average (M/D/1 queue).")
            queue["stable"] = True
            queue["wait_random_ms"] = round(wq, 6)
        e2e = tot.latency_ms + fill + (queue.get("wait_random_ms") or 0.0) + (
            next((e["ms"] for e in effects if e["id"] == "transfer" and not e["in_headline"]), 0.0) or 0.0)
        queue["end_to_end_ms"] = round(e2e, 6) if queue.get("stable", True) else None
    else:
        per_1000_s = 1000.0 / batch * tot.batch_ms / 1000.0
        queue["seconds_per_1000_inputs"] = round(per_1000_s, 6)
        add("offline", "Offline processing", per_1000_s * 1e3 / 1000.0, "inference (throughput)", False, "analytical",
            f"Inputs are processed back to back: about {per_1000_s:.3g} s per 1000 inputs; no queueing.")
    return {"effects": effects, "queue": queue, "headline_latency_ms": tot.latency_ms,
            "note": "The headline latency includes launch overhead. Batching, queueing, host transfers and start-up "
                    "are listed separately because they depend on how the system is fed."}  # fmt: skip
