"""Build a HardwareProfile from what a hardware engineer actually knows about their design.

Four starting points, one resulting profile:
  generic      peak TOPS per precision, memory bandwidth/size, TDP            (any board or SoC)
  mcu          core MHz, int8 MACs/cycle, flash, SRAM, run/sleep current       (STM32, nRF, ESP32...)
  fpga         DSP slices, clock, int8 packing, on-chip RAM, DDR, power report (Xilinx/Intel/Lattice)
  accelerator  TOPS, on-chip model memory, host link, board power             (Hailo, Coral, NPUs)

Whatever the user states is kept as entered (labelled vendor_spec if they cite a datasheet, else
assumed); everything derived here (clocks per power mode, cooling, power split) is labelled
`assumed` so the UI never presents it as measured.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.hardware import CoolingOption, HardwareProfile, PowerMode
from app.schemas.metrics import Provenance

A_ = Provenance.ASSUMED
V_ = Provenance.VENDOR_SPEC


class CustomHardwareSpec(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    vendor: str = "custom"
    style: Literal["generic", "mcu", "fpga", "accelerator"] = "generic"
    # True when the numbers come from a datasheet or vendor tool (shown as vendor_spec).
    from_datasheet: bool = False
    t_throttle_c: float | None = Field(None, gt=0, lt=200)
    notes: str = ""

    # generic / accelerator
    peak_tops: dict[str, float] = {}
    mem_bw_gbs: float | None = Field(None, gt=0)
    mem_gb: float | None = Field(None, gt=0)
    tdp_w: float | None = Field(None, gt=0)
    idle_w: float | None = Field(None, ge=0)
    onchip_weights_mb: float | None = Field(None, gt=0)
    host_link_gbs: float | None = Field(None, gt=0)

    # mcu
    core_mhz: float | None = Field(None, gt=0)
    int8_macs_per_cycle: float | None = Field(None, gt=0)
    has_fpu: bool = False
    flash_kb: float | None = Field(None, gt=0)
    sram_kb: float | None = Field(None, gt=0)
    run_power_mw: float | None = Field(None, gt=0)  # whole-chip power running the model
    sleep_power_mw: float | None = Field(None, ge=0)
    runtime_ram_kb: float = 16.0  # interpreter / arena bookkeeping beyond activations
    # Fraction of peak MACs a real kernel sustains (int8). MCU default 0.15 reflects depthwise-
    # heavy nets on CMSIS-NN-class kernels; set higher for a dedicated NPU.
    compute_efficiency: float | None = Field(None, gt=0, le=1.0)

    # fpga
    dsp_slices: int | None = Field(None, gt=0)
    fabric_mhz: float | None = Field(None, gt=0)
    int8_macs_per_dsp: float = 2.0  # DSP48E2-style packing; 1.0 if you do not pack
    achievable_dsp_use: float = Field(0.7, gt=0, le=1.0)
    bram_mb: float = 0.0
    uram_mb: float = 0.0
    static_w: float | None = Field(None, ge=0)
    dynamic_w: float | None = Field(None, gt=0)  # from the vendor power estimate at fabric_mhz

    # shared: board / package thermal and supply
    theta_ja_k_per_w: float | None = Field(None, gt=0)  # junction-to-ambient as built
    battery_wh: float | None = Field(None, gt=0)
    regulator_efficiency: float = Field(0.9, gt=0, le=1.0)

    @model_validator(mode="after")
    def _need_fields(self) -> CustomHardwareSpec:
        need: list[str] = {
            "generic": ["mem_bw_gbs", "mem_gb", "tdp_w"],
            "accelerator": ["mem_bw_gbs", "mem_gb", "tdp_w"],
            "mcu": ["core_mhz", "int8_macs_per_cycle", "flash_kb", "sram_kb", "run_power_mw"],
            "fpga": ["dsp_slices", "fabric_mhz", "mem_bw_gbs", "mem_gb", "dynamic_w"],
        }[self.style]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"{self.style} hardware needs: {', '.join(missing)}")
        if self.style in ("generic", "accelerator") and not any(
            v > 0 for v in self.peak_tops.values()
        ):
            raise ValueError("give peak throughput for at least one precision (fp32/fp16/int8)")
        return self


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "device"


def _modes(pmax_w: float, fracs: tuple[float, ...], gamma: float = 2.5) -> list[PowerMode]:
    out = []
    for f in fracs:
        cap = pmax_w * (0.35 + 0.65 * f**gamma) if f < 1 else pmax_w
        out.append(
            PowerMode(
                id=f"{int(round(f * 100))}pct", name=f"{int(round(f * 100))}% clock",
                cap_w=cap, freq_scale=f, mem_scale=max(0.4, min(1.0, 0.5 + 0.5 * f)),
            )
        )  # fmt: skip
    return out


def _cooling_from_theta(theta: float | None, pmax: float, t_thr: float, c_th: float):
    """User-stated thermal resistance, or a passive/fan pair sized so passive reaches ~70%."""
    if theta is not None:
        return [CoolingOption(id="as_built", name="As built (your θJA)", r_th_k_per_w=theta,
                              fan_w=0.0, c_th_j_per_k=c_th)], "as_built", V_  # fmt: skip
    r = max(0.7 * (t_thr - 25.0) / max(pmax, 1e-6), 0.2)
    return [
        CoolingOption(id="passive", name="Passive (assumed)", r_th_k_per_w=r, fan_w=0.0,
                      c_th_j_per_k=c_th),
        CoolingOption(id="active", name="With airflow (assumed)", r_th_k_per_w=0.6 * r,
                      fan_w=min(2.0, 0.04 * pmax), c_th_j_per_k=c_th),
    ], "passive", A_  # fmt: skip


def build_custom(spec: CustomHardwareSpec, device_id: str | None = None) -> HardwareProfile:
    s = spec
    src_val = V_ if s.from_datasheet else A_
    sources: dict[str, Provenance] = {}
    extras: dict[str, float | str] = {}
    kw: dict = {}
    notes = [s.notes] if s.notes else []

    if s.style == "mcu":
        macs = s.int8_macs_per_cycle
        assert macs and s.core_mhz and s.flash_kb and s.sram_kb and s.run_power_mw
        peak = {"int8": s.core_mhz * 1e6 * macs * 2 / 1e12}
        if s.has_fpu:
            peak["fp32"] = s.core_mhz * 1e6 * 2 / 1e12  # one FMA per cycle
        pmax = s.run_power_mw / 1e3
        idle = (s.sleep_power_mw if s.sleep_power_mw is not None else 0.02 * s.run_power_mw) / 1e3
        t_thr = s.t_throttle_c or 85.0
        modes = _modes(pmax, (1.0, 0.5, 0.25))
        cooling, default_cool, cprov = _cooling_from_theta(s.theta_ja_k_per_w or 50.0, pmax, t_thr, 0.5)
        kw = dict(
            peak_tflops=peak, mem_bw_gbs=4.0 * s.core_mhz * 1e6 / 1e9, mem_gb=s.sram_kb * 1024 / 1e9,
            weights_mem_gb=s.flash_kb * 1024 / 1e9, runtime_overhead_gb=s.runtime_ram_kb * 1024 / 1e9,
            usable_mem_fraction=0.9, eta_compute={"int8": s.compute_efficiency or 0.15, "fp32": 0.10}, eta_memory=0.5,
            launch_overhead_s=5000.0 / (s.core_mhz * 1e6),
            idle_w=idle, dyn_w_max=max(pmax - idle, 1e-6) * 0.8, mem_w_max=max(pmax - idle, 1e-6) * 0.2,
            leak_w_25c=0.01 * pmax, kind="mcu",
        )  # fmt: skip
        sources.update(peak_tflops=A_, mem_bw_gbs=A_, mem_gb=src_val, weights_mem_gb=src_val,
                       power_model=src_val if s.sleep_power_mw is not None else A_)
        extras.update(core_mhz=s.core_mhz, flash_kb=s.flash_kb, sram_kb=s.sram_kb)
        notes.append("Throughput = MHz x int8 MACs/cycle x 2, 15% achieved by default (kernel efficiency, "
                     "assumed). SRAM bandwidth assumed 4 bytes/cycle. Flash holds weights, SRAM "
                     "holds the largest layer's activations.")
    elif s.style == "fpga":
        assert s.dsp_slices and s.fabric_mhz and s.dynamic_w
        used = s.dsp_slices * s.achievable_dsp_use
        peak = {"int8": used * s.int8_macs_per_dsp * 2 * s.fabric_mhz * 1e6 / 1e12,
                "fp16": used * 0.25 * 2 * s.fabric_mhz * 1e6 / 1e12}
        static = s.static_w if s.static_w is not None else 0.5
        pmax = static + s.dynamic_w
        t_thr = s.t_throttle_c or 85.0
        modes = _modes(pmax, (1.0, 0.75, 0.5))
        cooling, default_cool, cprov = _cooling_from_theta(s.theta_ja_k_per_w, pmax, t_thr, 30.0)
        onchip = (s.bram_mb + s.uram_mb) or None
        kw = dict(
            peak_tflops=peak, mem_bw_gbs=s.mem_bw_gbs, mem_gb=s.mem_gb,
            onchip_weights_mb=onchip, eta_compute={"int8": 0.45, "fp16": 0.35}, eta_memory=0.65,
            launch_overhead_s=2e-6,
            idle_w=static * 0.8, dyn_w_max=s.dynamic_w * 0.75, mem_w_max=s.dynamic_w * 0.25,
            leak_w_25c=static * 0.2, kind="fpga",
        )  # fmt: skip
        sources.update(peak_tflops=A_, mem_bw_gbs=src_val, mem_gb=src_val,
                       power_model=src_val if s.static_w is not None else A_)
        extras.update(dsp_slices=s.dsp_slices, fabric_mhz=s.fabric_mhz, bram_mb=s.bram_mb,
                      uram_mb=s.uram_mb)
        notes.append(f"Peak = {used:.0f} usable DSPs ({s.achievable_dsp_use:.0%} of "
                     f"{s.dsp_slices}) x {s.int8_macs_per_dsp:g} int8 MACs x 2 x {s.fabric_mhz:g} MHz; "
                     "achieved efficiency assumed 45% (depends entirely on your dataflow design: "
                     "replace it with your HLS / RTL estimate when you have one).")
    else:  # generic or accelerator
        assert s.tdp_w and s.mem_bw_gbs and s.mem_gb
        pmax = s.tdp_w
        t_thr = s.t_throttle_c or 85.0
        modes = _modes(pmax, (1.0, 0.7, 0.4))
        cooling, default_cool, cprov = _cooling_from_theta(s.theta_ja_k_per_w, pmax, t_thr, max(5.0, 6.0 * pmax))
        idle = s.idle_w if s.idle_w is not None else 0.12 * pmax
        rest = max(pmax - idle, 1e-6)
        peak = {k: v for k, v in s.peak_tops.items() if v > 0}
        kw = dict(
            peak_tflops=peak, mem_bw_gbs=s.mem_bw_gbs, mem_gb=s.mem_gb,
            onchip_weights_mb=s.onchip_weights_mb,
            idle_w=idle, dyn_w_max=0.65 * rest, mem_w_max=0.25 * rest, leak_w_25c=0.10 * rest,
            kind="accelerator" if s.style == "accelerator" else "custom",
        )  # fmt: skip
        sources.update(peak_tflops=src_val, mem_bw_gbs=src_val, mem_gb=src_val,
                       power_model=src_val if s.idle_w is not None else A_)
        if s.host_link_gbs:
            extras["host_link_gbs"] = s.host_link_gbs
            notes.append(f"Host link {s.host_link_gbs:g} GB/s is recorded but not yet a limit in "
                         "the model: for USB/PCIe accelerators measure end to end.")

    if s.battery_wh:
        extras["battery_wh"] = s.battery_wh
    extras["regulator_efficiency"] = s.regulator_efficiency
    sources.update(power_modes=A_, thermal=cprov)
    return HardwareProfile(
        id=device_id or f"custom-{_slug(s.name)}",
        name=s.name, vendor=s.vendor,
        role="your hardware (not measured)",
        gamma=2.5, t_throttle_c=t_thr, t_shutdown_c=t_thr + 15.0,
        power_modes=modes, cooling_options=cooling, default_cooling=default_cool,
        sources=sources, extras=extras, verified=False, alternative_candidate=True,
        notes=" ".join(notes + ["Clock steps, power split and cooling are assumptions: refine "
                                "with a measurement (Real measurements page)."]),
        **kw,
    )  # fmt: skip
