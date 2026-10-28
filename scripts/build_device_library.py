"""Regenerate the non-Jetson device library (backend/app/data/devices/*.yaml).

Every entry states, per field group, whether the number is a vendor figure (`vendor_spec`) or
something we derived or guessed (`assumed`). Throughput is DENSE (sparse marketing figures are
halved); where a vendor quotes one "TOPS" number it is treated as int8. Nothing here is measured.

    backend/.venv/Scripts/python scripts/build_device_library.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.hwbuild import CustomHardwareSpec, build_custom  # noqa: E402
from app.schemas.hardware import CoolingOption, HardwareProfile, PowerMode  # noqa: E402
from app.schemas.metrics import Provenance as P  # noqa: E402

OUT = ROOT / "backend" / "app" / "data" / "devices"
V, A = P.VENDOR_SPEC, P.ASSUMED


def modes(items):
    """items: (id, name, cap_w, freq_scale, mem_scale)"""
    return [PowerMode(id=i, name=n, cap_w=c, freq_scale=f, mem_scale=m) for i, n, c, f, m in items]


def cool(items):
    """items: (id, name, r_th, fan_w, c_th)"""
    return [CoolingOption(id=i, name=n, r_th_k_per_w=r, fan_w=f, c_th_j_per_k=c)
            for i, n, r, f, c in items]  # fmt: skip


def power_split(pmax, idle):
    rest = pmax - idle
    return dict(idle_w=idle, dyn_w_max=0.65 * rest, mem_w_max=0.25 * rest, leak_w_25c=0.10 * rest)


DEVICES: list[HardwareProfile] = []


def add(**kw):
    kw.setdefault("gamma", 2.5)
    kw.setdefault("verified", False)
    DEVICES.append(HardwareProfile(**kw))


# ---- single-board computers and accelerator modules ---------------------------------------------

add(
    id="raspberry-pi-5", name="Raspberry Pi 5 (8 GB, CPU only)", vendor="Raspberry Pi",
    kind="sbc", role="general-purpose edge board, CPU inference",
    peak_tflops={"fp32": 0.154, "fp16": 0.307, "int8": 0.61},
    mem_bw_gbs=17.0, mem_gb=8.0, **power_split(8.0, 2.7),
    t_throttle_c=80.0, t_shutdown_c=90.0,
    power_modes=modes([("powersave", "1.5 GHz cap", 5.0, 0.625, 0.8),
                       ("default", "2.4 GHz", 8.0, 1.0, 1.0)]),
    cooling_options=cool([("bare", "No heatsink", 6.0, 0.0, 15), ("heatsink", "Passive heatsink", 3.5, 0.0, 40),
                          ("active", "Official active cooler", 1.8, 0.4, 40)]),
    default_cooling="bare",
    sources={"peak_tflops": A, "mem_bw_gbs": V, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="Peak is derived from 4x Cortex-A76 at 2.4 GHz with two 128-bit NEON pipes (fp32 4 lanes, "
          "fp16 8 lanes, int8 SDOT 16 MACs); real frameworks reach a small fraction. LPDDR4X-4267 on a "
          "32-bit bus = 17 GB/s. The firmware throttles at 80 C. Power and cooling are estimates.",
)
add(
    id="raspberry-pi-5-hailo-8l", name="Raspberry Pi 5 + Hailo-8L (AI Kit)", vendor="Raspberry Pi / Hailo",
    kind="accelerator", role="edge board with 13 TOPS vision accelerator over PCIe",
    peak_tflops={"int8": 13.0}, mem_bw_gbs=1.0, mem_gb=8.0, onchip_weights_mb=100.0, onchip_bw_gbs=50.0,
    **power_split(8.0, 3.0), t_throttle_c=80.0, t_shutdown_c=90.0,
    power_modes=modes([("default", "Default", 8.0, 1.0, 1.0)]),
    cooling_options=cool([("bare", "No heatsink", 6.0, 0.0, 15), ("heatsink", "Passive heatsink", 3.5, 0.0, 40),
                          ("active", "Official active cooler", 1.8, 0.4, 40)]),
    default_cooling="heatsink", eta_compute={"int8": 0.30},
    sources={"peak_tflops": V, "mem_bw_gbs": A, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="13 TOPS int8 is Hailo's figure for the 8L. The host link (PCIe, about 1 GB/s on a Pi 5 x1 "
          "lane) is recorded as the external bandwidth; weights are ASSUMED resident on chip "
          "(Hailo compiles large networks into contexts the model cannot see), so large models are "
          "optimistic. Board power is the Pi plus module estimate. Measure.",
    extras={"host_link_gbs": 1.0},
)
add(
    id="hailo-8", name="Hailo-8 (M.2, 26 TOPS)", vendor="Hailo", kind="accelerator",
    role="vision accelerator module (needs a host)",
    peak_tflops={"int8": 26.0}, mem_bw_gbs=3.9, mem_gb=8.0, onchip_weights_mb=100.0, onchip_bw_gbs=100.0,
    **power_split(3.5, 0.5), t_throttle_c=85.0, t_shutdown_c=105.0,
    power_modes=modes([("typical", "Typical", 3.5, 1.0, 1.0)]),
    cooling_options=cool([("module", "M.2 module, small spreader", 12.0, 0.0, 8),
                          ("heatsink", "Module with heatsink", 7.0, 0.0, 25)]),
    default_cooling="module", eta_compute={"int8": 0.30},
    sources={"peak_tflops": V, "mem_bw_gbs": A, "mem_gb": A, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="26 TOPS int8 from Hailo. Published power figures conflict (about 2.5-3.5 W typical in "
          "vendor material, up to about 8.65 W in some module datasheets); 3.5 W is used. Host "
          "link is PCIe Gen3 x4 (about 3.9 GB/s). Weights assumed resident (see Hailo-8L note). "
          "Host memory and host CPU power are NOT included.",
    extras={"host_link_gbs": 3.9},
)
add(
    id="coral-edge-tpu", name="Google Coral Edge TPU (USB accelerator)", vendor="Google",
    kind="accelerator", role="int8-only TFLite accelerator",
    peak_tflops={"int8": 4.0}, mem_bw_gbs=0.4, mem_gb=8.0, onchip_weights_mb=8.0, onchip_bw_gbs=64.0,
    **power_split(2.0, 0.4), t_throttle_c=85.0, t_shutdown_c=100.0,
    power_modes=modes([("std", "Standard clock", 1.5, 0.6, 0.8), ("max", "Maximum clock", 2.0, 1.0, 1.0)]),
    cooling_options=cool([("bare", "Case only", 9.0, 0.0, 6), ("heatsink", "Case + airflow", 5.0, 0.0, 10)]),
    default_cooling="bare", eta_compute={"int8": 0.35},
    sources={"peak_tflops": V, "mem_bw_gbs": A, "mem_gb": A, "power_model": V, "thermal": A,
             "power_modes": V},
    notes="4 TOPS int8 and about 2 W from Google. Models larger than the roughly 8 MB of on-chip "
          "memory stream their weights over USB (effective 0.4 GB/s assumed) and slow sharply: that "
          "cliff is modelled. Only int8 TFLite models compile for it. Host power not included.",
    extras={"host_link_gbs": 0.4},
)

# ---- datacenter / workstation GPUs --------------------------------------------------------------

add(
    id="nvidia-l4", name="NVIDIA L4 (24 GB)", vendor="NVIDIA", kind="datacenter_gpu",
    role="inference GPU, passive server card",
    peak_tflops={"fp32": 30.3, "fp16": 121.0, "int8": 242.0}, mem_bw_gbs=300.0, mem_gb=24.0,
    **power_split(72.0, 12.0), t_throttle_c=85.0, t_shutdown_c=95.0,
    power_modes=modes([("50w", "50 W cap", 50.0, 0.7, 1.0), ("72w", "72 W (stock)", 72.0, 1.0, 1.0)]),
    cooling_options=cool([("server_low", "Server airflow, low", 0.6, 0.0, 400),
                          ("server_high", "Server airflow, high", 0.35, 0.0, 400)]),
    default_cooling="server_low", alternative_candidate=True,
    sources={"peak_tflops": V, "mem_bw_gbs": V, "mem_gb": V, "power_modes": V, "power_model": A,
             "thermal": A},
    notes="Dense tensor-core figures (NVIDIA quotes 242/485 TFLOPS/TOPS WITH sparsity for fp16/int8; "
          "halved here). Passive card: temperature depends entirely on chassis airflow.",
)
add(
    id="nvidia-t4", name="NVIDIA T4 (16 GB)", vendor="NVIDIA", kind="datacenter_gpu",
    role="previous-generation inference GPU, common in cloud",
    peak_tflops={"fp32": 8.1, "fp16": 65.0, "int8": 130.0}, mem_bw_gbs=320.0, mem_gb=16.0,
    **power_split(70.0, 10.0), t_throttle_c=85.0, t_shutdown_c=95.0,
    power_modes=modes([("70w", "70 W (stock)", 70.0, 1.0, 1.0)]),
    cooling_options=cool([("server_low", "Server airflow, low", 0.6, 0.0, 400),
                          ("server_high", "Server airflow, high", 0.35, 0.0, 400)]),
    default_cooling="server_low",
    sources={"peak_tflops": V, "mem_bw_gbs": V, "mem_gb": V, "power_modes": V, "power_model": A,
             "thermal": A},
    notes="NVIDIA's T4 tensor figures are dense. Passive card; chassis airflow sets temperature.",
)

# ---- laptop / PC SoCs with NPUs -----------------------------------------------------------------

add(
    id="apple-m4", name="Apple M4 (Neural Engine + GPU, 16 GB)", vendor="Apple", kind="laptop_soc",
    role="laptop / tablet SoC; Core ML runs on the Neural Engine, GPU or CPU",
    peak_tflops={"fp32": 4.3, "fp16": 8.6, "int8": 38.0}, mem_bw_gbs=120.0, mem_gb=16.0,
    **power_split(22.0, 3.0), t_throttle_c=100.0, t_shutdown_c=110.0,
    power_modes=modes([("quiet", "Low power", 12.0, 0.6, 0.9), ("default", "Default", 22.0, 1.0, 1.0)]),
    cooling_options=cool([("passive", "Fanless chassis", 2.2, 0.0, 120), ("fan", "Fan", 1.3, 0.8, 120)]),
    default_cooling="passive",
    sources={"peak_tflops": A, "mem_bw_gbs": V, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="Apple quotes 38 TOPS for the Neural Engine without naming the precision (treated as int8: "
          "marketing figure). GPU fp32 about 4.3 TFLOPS is derived, fp16 assumed double. The model "
          "cannot know which engine Core ML picks. Memory bandwidth 120 GB/s is Apple's figure.",
)
add(
    id="snapdragon-x-elite", name="Qualcomm Snapdragon X Elite (Hexagon NPU)", vendor="Qualcomm",
    kind="laptop_soc", role="Copilot+ laptop SoC, NPU inference via QNN / ONNX Runtime",
    peak_tflops={"fp32": 4.6, "fp16": 9.2, "int8": 45.0}, mem_bw_gbs=135.0, mem_gb=16.0,
    **power_split(23.0, 3.0), t_throttle_c=100.0, t_shutdown_c=110.0,
    power_modes=modes([("eco", "Efficiency", 15.0, 0.7, 0.9), ("default", "Default 23 W", 23.0, 1.0, 1.0),
                       ("boost", "Boost", 45.0, 1.0, 1.0)]),
    cooling_options=cool([("passive", "Thin chassis", 2.0, 0.0, 100), ("fan", "Fan", 1.1, 0.8, 100)]),
    default_cooling="fan",
    sources={"peak_tflops": A, "mem_bw_gbs": V, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": V},
    notes="45 TOPS is Qualcomm's NPU figure (int8). GPU fp32 about 4.6 TFLOPS from the public spec, "
          "fp16 assumed double. 135 GB/s from LPDDR5X-8448 on 128 bits.",
)
add(
    id="qualcomm-qcs6490", name="Qualcomm QCS6490 (RB3 Gen 2 / Dragonwing)", vendor="Qualcomm",
    kind="edge_soc", role="IoT / robotics SoC with a 12 TOPS Hexagon NPU",
    peak_tflops={"fp32": 0.8, "fp16": 1.6, "int8": 12.0}, mem_bw_gbs=25.6, mem_gb=8.0,
    **power_split(9.0, 1.5), t_throttle_c=95.0, t_shutdown_c=105.0,
    power_modes=modes([("low", "Low power", 5.0, 0.6, 0.8), ("default", "Default", 9.0, 1.0, 1.0)]),
    cooling_options=cool([("passive", "Small heatsink", 5.0, 0.0, 30), ("fan", "Heatsink + fan", 2.5, 0.8, 30)]),
    default_cooling="passive",
    sources={"peak_tflops": A, "mem_bw_gbs": A, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="12 TOPS is Qualcomm's headline NPU figure (int8). GPU figures and memory bandwidth are "
          "estimates; power is a rough budget for a dev kit. Treat as order-of-magnitude.",
)
add(
    id="intel-core-ultra-7-258v", name="Intel Core Ultra 7 258V (NPU 4)", vendor="Intel",
    kind="laptop_soc", role="Lunar Lake laptop SoC, inference via OpenVINO on the NPU",
    peak_tflops={"fp16": 24.0, "int8": 48.0}, mem_bw_gbs=136.0, mem_gb=32.0,
    **power_split(30.0, 4.0), t_throttle_c=100.0, t_shutdown_c=110.0,
    power_modes=modes([("quiet", "17 W", 17.0, 0.65, 0.9), ("perf", "30 W", 30.0, 1.0, 1.0)]),
    cooling_options=cool([("thin", "Thin laptop", 2.0, 0.0, 100), ("fan", "Fan", 1.2, 0.8, 100)]),
    default_cooling="fan",
    sources={"peak_tflops": A, "mem_bw_gbs": A, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": A},
    notes="Intel quotes 48 TOPS for NPU 4 (some material says 47); fp16 at half that is assumed. "
          "136 GB/s is OUR estimate from LPDDR5X-8533 on 128 bits (Intel does not publish it). "
          "The 67 TOPS GPU is not modelled here.",
)
add(
    id="amd-ryzen-ai-9-hx-370", name="AMD Ryzen AI 9 HX 370 (XDNA 2 NPU)", vendor="AMD",
    kind="laptop_soc", role="Strix Point laptop SoC, NPU via Ryzen AI / ONNX Runtime",
    peak_tflops={"fp16": 25.0, "int8": 50.0}, mem_bw_gbs=120.0, mem_gb=32.0,
    **power_split(28.0, 4.0), t_throttle_c=100.0, t_shutdown_c=110.0,
    power_modes=modes([("eco", "15 W", 15.0, 0.65, 0.9), ("default", "28 W", 28.0, 1.0, 1.0)]),
    cooling_options=cool([("thin", "Thin laptop", 2.0, 0.0, 100), ("fan", "Fan", 1.2, 0.8, 100)]),
    default_cooling="fan",
    sources={"peak_tflops": A, "mem_bw_gbs": V, "mem_gb": V, "power_model": A, "thermal": A,
             "power_modes": V},
    notes="AMD quotes 50 TOPS for the XDNA 2 NPU (block-fp16/int8 class; fp16 at half is assumed). "
          "120 GB/s from LPDDR5X-7500 on 128 bits.",
)

# ---- microcontrollers and FPGAs through the same builders the wizard uses ----------------------

BUILT = [
    ("stm32h743", CustomHardwareSpec(
        name="STM32H743 (Cortex-M7, 480 MHz)", vendor="STMicroelectronics", style="mcu",
        core_mhz=480, int8_macs_per_cycle=2, has_fpu=True, flash_kb=2048, sram_kb=1024,
        run_power_mw=330, sleep_power_mw=5, from_datasheet=True, t_throttle_c=85,
        notes="2 MB flash and about 1 MB RAM (not all contiguous or fast); ~330 mW running from "
              "datasheet current figures x 3.3 V. CMSIS-NN typically reaches 1-2 MACs/cycle.")),
    ("nrf52840", CustomHardwareSpec(
        name="Nordic nRF52840 (Cortex-M4F, 64 MHz)", vendor="Nordic", style="mcu",
        core_mhz=64, int8_macs_per_cycle=1, has_fpu=True, flash_kb=1024, sram_kb=256,
        run_power_mw=20, sleep_power_mw=0.01, from_datasheet=True, t_throttle_c=85,
        notes="Tiny-ML class part: about 20 mW running, microwatts asleep. Radio not modelled.")),
    ("esp32-s3", CustomHardwareSpec(
        name="Espressif ESP32-S3 (2x LX7, 240 MHz)", vendor="Espressif", style="mcu",
        core_mhz=240, int8_macs_per_cycle=8, has_fpu=True, flash_kb=8192, sram_kb=512,
        run_power_mw=250, sleep_power_mw=1, from_datasheet=True, t_throttle_c=85,
        notes="Eight int8 MACs/cycle on one core is a conservative reading of ESP-NN vector kernels (the S3 vector unit peaks at 16). "
              "Flash 8 MB is board-dependent; PSRAM is not modelled. Wi-Fi/BLE power excluded.")),
    ("stm32n6", CustomHardwareSpec(
        name="STM32N6 (Neural-ART NPU, ~600 GOPS)", vendor="STMicroelectronics", style="mcu",
        core_mhz=800, int8_macs_per_cycle=375, has_fpu=False, flash_kb=65536, sram_kb=4200,
        run_power_mw=600, sleep_power_mw=20, runtime_ram_kb=64, compute_efficiency=0.30, from_datasheet=False, t_throttle_c=105,
        notes="Modelled as 800 MHz x 375 MAC/cycle = 600 GOPS int8 (ST's Neural-ART headline). Weights "
              "live in EXTERNAL flash whose size depends on your board (64 MB assumed); 4.2 MB "
              "on-chip RAM holds activations. Running power is a guess: measure.")),
    ("xilinx-kria-kv260", CustomHardwareSpec(
        name="AMD Kria KV260 (Zynq UltraScale+ ZU5EV)", vendor="AMD / Xilinx", style="fpga",
        dsp_slices=1248, fabric_mhz=300, int8_macs_per_dsp=2, achievable_dsp_use=0.7, bram_mb=0.64,
        uram_mb=2.25, mem_bw_gbs=19.2, mem_gb=4.0, static_w=1.5, dynamic_w=5.0, from_datasheet=False,
        t_throttle_c=85,
        notes="DPU-class design at 300 MHz is about 1.2 TOPS in AMD material; the 70% DSP use here "
              "lands near that. Static and dynamic power are rough: replace with your Vivado "
              "report_power numbers.")),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, spec in BUILT:
        hw = build_custom(spec, slug)
        hw = hw.model_copy(update={"role": "reference design for this class of chip (not measured)"})
        DEVICES.append(hw)
    for hw in DEVICES:
        data = hw.model_dump(mode="json", exclude_none=True)
        data = {k: v for k, v in data.items() if v not in ({}, [], "")}
        (OUT / f"{hw.id}.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
        )
        print("wrote", hw.id)


if __name__ == "__main__":
    main()
