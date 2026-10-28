"""Deployment and measurement scripts per hardware ecosystem.

HONEST STATUS: every script here is a TEMPLATE written from each vendor's public documentation. None
has been run on a device by us. Tool names and flags change between releases, so each script says
which command to look up when a flag is version-dependent instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectSpec


@dataclass(frozen=True)
class Eco:
    key: str
    label: str
    files: dict[str, tuple[str, str]]  # name -> (description, content)
    steps: list[str]


def ecosystem_key(hw: HardwareProfile) -> str:
    i, n = hw.id.lower(), hw.name.lower()
    if "jetson" in i:
        return "jetson"
    if "hailo" in i:
        return "hailo"
    if "coral" in i or "edge tpu" in n:
        return "coral"
    if "raspberry" in i:
        return "raspberry"
    if "apple" in i:
        return "apple"
    if "snapdragon" in i or "qualcomm" in i:
        return "qualcomm"
    if "intel" in i:
        return "intel"
    if "amd-ryzen" in i:
        return "amd_npu"
    if hw.kind == "datacenter_gpu" or i.startswith("nebius-") or i.startswith("nvidia-"):
        return "nvidia_gpu"
    if hw.kind == "mcu":
        return "mcu"
    if hw.kind == "fpga":
        return "fpga"
    return "generic"


def _tflite_int8_py(model: ModelProfile) -> str:
    return '''"""Convert a TensorFlow/Keras model to a fully-int8 TFLite file (needed by MCUs, Edge TPU, Hailo).

    pip install tensorflow numpy
    python quantise_tflite.py --saved-model ./saved_model --calib calibration.npy --out model_int8.tflite

`calibration.npy` is an array of 100-500 REAL input samples shaped like the model input
(N, ...). Random data gives poor scales: re-measure accuracy on your validation set afterwards.
For a PyTorch model export to ONNX and use onnx2tf / ai-edge-torch first.
"""

import argparse

import numpy as np
import tensorflow as tf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saved-model", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--out", default="model_int8.tflite")
    a = ap.parse_args()
    data = np.load(a.calib).astype(np.float32)

    def reps():
        for x in data[:500]:
            yield [x[None, ...]]

    conv = tf.lite.TFLiteConverter.from_saved_model(a.saved_model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = reps
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    open(a.out, "wb").write(conv.convert())
    print("wrote", a.out)


if __name__ == "__main__":
    main()
'''


def _post_report(spec: ProjectSpec, cfg: Config, fmt: str) -> str:
    return (
        '# Send the measured latency back (Verify tab > "Add one measurement" has the same fields):\n'
        'APP="${APP:-http://localhost:8000}"\n'
        f'echo "Latency/power measured? POST them to $APP/api/v1/measurements (device_id={spec.device_id}, '
        f'model_id={spec.model_id}, precision={cfg.precision}, batch={cfg.batch})"\n'
        f'# power meter CSV: curl -s -X POST "$APP/api/v1/hardware/parse-report" -H "content-type: application/json" '
        f"-d @- <<< '{{\"text\": \"<csv text>\", \"tool\": \"power_csv\", \"supply_v\": 5.0}}'\n"
    )


def build(spec: ProjectSpec, model: ModelProfile, hw: HardwareProfile, cfg: Config) -> Eco:
    key = ecosystem_key(hw)
    p = cfg.precision
    onnx = f"model_{p}.onnx"
    files: dict[str, tuple[str, str]] = {}
    steps: list[str] = []

    if key == "nvidia_gpu":
        files["build_engine.sh"] = ("Build a TensorRT engine", f"""#!/usr/bin/env bash
# Generated template, not run. Check `trtexec --help` for your TensorRT version.
set -euo pipefail
trtexec --onnx={onnx} --saveEngine=model_{p}.plan {"--" + p if p != "fp32" else ""} --shapes=input:{cfg.batch}x3x224x224
""")
        files["set_power_limit.sh"] = ("Cap the GPU board power", f"""#!/usr/bin/env bash
# Generated template, not run. Needs root. Valid range: nvidia-smi -q -d POWER
set -euo pipefail
sudo nvidia-smi -q -d POWER | grep -E "Min Power Limit|Max Power Limit|Default Power Limit"
# sudo nvidia-smi -pl <WATTS>     # <- the recommended cap is about {hw.mode(cfg.power_mode_id).cap_w:.0f} W
nvidia-smi --query-gpu=power.draw,temperature.gpu,clocks.sm,clocks_throttle_reasons.active --format=csv -l 1
""")
        steps = [f"`bash build_engine.sh` - build the TensorRT engine ({p}).",
                 "`bash set_power_limit.sh` - set the board power cap and watch power/clocks.",
                 "Benchmark and import with `bench/` or the Verify tab (nvidia-smi CSV import is supported)."]
    elif key == "raspberry":
        files["run_tflite.py"] = ("Benchmark with TFLite + XNNPACK", """\"\"\"Time a TFLite model on the Raspberry Pi. pip install tflite-runtime numpy\"\"\"
import sys, time
import numpy as np
from tflite_runtime.interpreter import Interpreter

it = Interpreter(model_path=sys.argv[1], num_threads=4)
it.allocate_tensors()
inp = it.get_input_details()[0]
x = np.zeros(inp["shape"], dtype=inp["dtype"])
for _ in range(10):
    it.set_tensor(inp["index"], x); it.invoke()
ts = []
for _ in range(100):
    t = time.perf_counter(); it.set_tensor(inp["index"], x); it.invoke(); ts.append((time.perf_counter() - t) * 1e3)
ts.sort(); print("batch_latency_ms p50", ts[50], "p95", ts[95])
""")
        files["monitor_pi.sh"] = ("Log temperature, throttling flags and clock", """#!/usr/bin/env bash
# Generated template, not run. vcgencmd ships with Raspberry Pi OS.
while true; do
  echo "$(date +%s) $(vcgencmd measure_temp) $(vcgencmd get_throttled) $(vcgencmd measure_clock arm)"
  sleep 1
done
# get_throttled bit 2 = currently throttled, bit 18 = throttling has occurred since boot.
# For wall power use a USB power meter or Joulescope/PPK2; the Pi does not report it reliably.
""")
        files["quantise_tflite.py"] = ("Convert to int8 TFLite", _tflite_int8_py(model))
        steps = ["`python quantise_tflite.py` - make the int8 TFLite model (on a PC).",
                 "Copy it to the Pi; `python run_tflite.py model_int8.tflite` for latency.",
                 "`bash monitor_pi.sh` in a second terminal to see temperature and throttling."]
    elif key == "hailo":
        files["quantise_tflite.py"] = ("Prepare a calibration-quantisable model", _tflite_int8_py(model))
        files["compile_hailo.sh"] = ("Compile for Hailo and benchmark", f"""#!/usr/bin/env bash
# Generated template, not run. Needs the Hailo Dataflow Compiler (hailo_sdk_client) on an x86 host
# and HailoRT on the target. Exact commands depend on your DFC / Model Zoo version:
set -euo pipefail
# 1) Parse + optimise (quantise with real calibration images) + compile to a .hef:
#    hailo parser onnx {onnx}   ;  hailo optimize --calib-set-path calib.npy model.har  ;  hailo compiler model_optimized.har
#    or the Model Zoo: hailomz compile --ckpt {onnx} --hw-arch <hailo8|hailo8l> --calib-path calib/
# 2) On the target:
hailortcli fw-control identify
hailortcli benchmark model.hef      # FPS and latency
# Power: `hailortcli measure-power` exists on some builds (check `hailortcli --help`); otherwise
# measure the board supply and import the CSV.
""")
        steps = ["Compile on an x86 host with the Hailo Dataflow Compiler (`compile_hailo.sh` lists the steps).",
                 "Benchmark on the target with `hailortcli benchmark` and measure supply power."]
    elif key == "coral":
        files["quantise_tflite.py"] = ("Convert to int8 TFLite", _tflite_int8_py(model))
        files["compile_edgetpu.sh"] = ("Compile for the Edge TPU", """#!/usr/bin/env bash
# Generated template, not run. edgetpu_compiler runs on x86 Linux (Debian/Ubuntu).
set -euo pipefail
edgetpu_compiler -s model_int8.tflite
# The log shows how many ops mapped to the Edge TPU; ops left on the CPU make it slow. Models above
# ~8 MB stream weights over USB: this tool's prediction models that cliff.
""")
        steps = ["`python quantise_tflite.py` - fully-int8 TFLite.", "`bash compile_edgetpu.sh` - check every op mapped.",
                 "Time it on the host with pycoral; measure the USB supply for power."]
    elif key == "apple":
        files["convert_coreml.py"] = ("Convert to Core ML", f"""\"\"\"pip install coremltools torch. Convert a traced PyTorch model.\"\"\"
import coremltools as ct, torch

model = torch.load("model.pt").eval()
x = torch.zeros(1, 3, 224, 224)
ml = ct.convert(torch.jit.trace(model, x), inputs=[ct.TensorType(shape=x.shape)],
                compute_precision=ct.precision.{"FLOAT16" if p != "fp32" else "FLOAT32"},
                compute_units=ct.ComputeUnit.ALL)
ml.save("model.mlpackage")
# int8 weights: coremltools.optimize.coreml (palettize / linear_quantize_weights).
""")
        files["measure_power.sh"] = ("CPU/GPU/ANE power with powermetrics", """#!/usr/bin/env bash
# Generated template, not run. Needs sudo. Run your inference loop in another terminal.
sudo powermetrics --samplers cpu_power,gpu_power,ane_power -i 1000 -n 30 | tee powermetrics.log
# Look at 'Combined Power (CPU + GPU + ANE)'. Xcode Instruments (Core ML template) shows which
# compute unit actually ran each layer.
""")
        steps = ["`python convert_coreml.py` - Core ML package.", "`bash measure_power.sh` while running the model."]
    elif key == "qualcomm":
        files["qualcomm_notes.md"] = ("Qualcomm AI Hub / QNN path", """# Qualcomm path (template, not run)

Easiest: Qualcomm AI Hub (`pip install qai-hub`), which compiles, profiles and runs on real hosted
devices:

```python
import qai_hub as hub
job = hub.submit_compile_job(model="model.onnx", device=hub.Device("Snapdragon X Elite CRD"),
                             input_specs={"input": ((1, 3, 224, 224), "float32")},
                             options="--quantize_full_type int8")
```

Offline: QNN SDK (`qnn-onnx-converter`, `qnn-model-lib-generator`, `qnn-net-run`) - flags vary by SDK
version, check the SDK docs. Measure supply power for the board (QCS6490 dev kits expose rails).
""")
        steps = ["Follow `qualcomm_notes.md` (AI Hub profile job gives measured latency on real silicon)."]
    elif key == "intel":
        files["run_openvino.sh"] = ("Convert and benchmark with OpenVINO", f"""#!/usr/bin/env bash
# Generated template, not run. pip install openvino
set -euo pipefail
ovc {onnx} --output_model model_ov.xml --compress_to_fp16={"true" if p != "fp32" else "false"}
benchmark_app -m model_ov.xml -d NPU -hint latency -t 30     # try -d GPU and -d CPU to compare
# int8: quantise with NNCF (pip install nncf) using real calibration data, then benchmark again.
# Power: RAPL via `sudo turbostat` or HWiNFO/Intel Power Gadget on Windows; the NPU rail may not
# be separate, so measure package power with and without the load.
""")
        steps = ["`bash run_openvino.sh` - convert and benchmark on NPU / GPU / CPU."]
    elif key == "amd_npu":
        files["run_ryzen_ai.py"] = ("ONNX Runtime with the Ryzen AI provider", f"""\"\"\"Template, not run. Needs the Ryzen AI Software (VitisAI execution provider) installed.
Quantise first with AMD Quark (int8 / bf16) using real calibration data.\"\"\"
import time, numpy as np, onnxruntime as ort

sess = ort.InferenceSession("{onnx}", providers=["VitisAIExecutionProvider"],
                            provider_options=[{{"config_file": "vaip_config.json"}}])
inp = sess.get_inputs()[0]
x = np.zeros([d if isinstance(d, int) else 1 for d in inp.shape], dtype=np.float32)
for _ in range(10): sess.run(None, {{inp.name: x}})
ts = []
for _ in range(100):
    t = time.perf_counter(); sess.run(None, {{inp.name: x}}); ts.append((time.perf_counter() - t) * 1e3)
ts.sort(); print("batch_latency_ms p50", ts[50], "p95", ts[95])
""")
        steps = ["Quantise with AMD Quark, then `python run_ryzen_ai.py` for latency; measure package power."]
    elif key == "mcu":
        files["quantise_tflite.py"] = ("Convert to int8 TFLite", _tflite_int8_py(model))
        files["deploy_mcu.md"] = ("Flash/SRAM check and deployment per toolchain", f"""# Deploy on {hw.name} (template, not run)

Your tool predicted: weights storage {(hw.weights_mem_gb or 0) * 1e3:.2f} MB,
SRAM {hw.mem_gb * 1e3:.3f} MB. Confirm both with your toolchain's own report before trusting this:

- **STM32 (ST Edge AI Core):** `stedgeai analyze --model model_int8.tflite --target stm32h7`
  prints weights (ro), activations (rw) and MACC. Paste it into this tool (Hardware > Read a vendor
  report) to compare. `stedgeai generate` then produces C code for STM32CubeIDE.
- **Arm Cortex-M (TFLite Micro + CMSIS-NN):** `xxd -i model_int8.tflite > model_data.cc`, set a tensor
  arena of at least the activations figure, build with CMSIS-NN kernels enabled.
- **Ethos-U (Arm Vela):** `vela model_int8.tflite --accelerator-config ethos-u55-128` prints SRAM /
  flash use and an estimated cycle count.
- **ESP32 (esp-tflite-micro + ESP-NN):** use the ESP-IDF component; arena in internal RAM or PSRAM.
- **Latency:** toggle a GPIO around `Invoke()` and read it on a scope/logic analyser, or use the
  DWT cycle counter (`DWT->CYCCNT`) divided by the core clock.
- **Power:** put a Joulescope / Otii / PPK2 in series with the supply, run inference in a loop with a
  sleep between, export CSV and use *Import power-meter CSV* in the app.
""")
        steps = ["`python quantise_tflite.py` - int8 TFLite.", "Follow `deploy_mcu.md` for your toolchain.",
                 "Measure latency (GPIO/DWT) and power (meter CSV) and import them."]
    elif key == "fpga":
        files["hls4ml_flow.py"] = ("hls4ml flow (Keras/PyTorch to HLS)", """\"\"\"Template, not run. pip install hls4ml. Vivado/Vitis HLS must be installed for synthesis.\"\"\"
import hls4ml

config = hls4ml.utils.config_from_keras_model(model, granularity="name")   # `model` = your Keras model
config["Model"]["Precision"] = "ap_fixed<8,3>"      # int8-like; tune per layer
config["Model"]["ReuseFactor"] = 16                 # higher = fewer DSPs, more latency
hls = hls4ml.converters.convert_from_keras_model(model, hls_config=config, output_dir="hls_prj",
                                                part="xczu5ev-sfvc784-1-e", io_type="io_stream")
hls.compile()
hls.build(csim=False, synth=True, vsynth=True)      # then read hls_prj/.../*_csynth.rpt
hls4ml.report.read_vivado_report("hls_prj")
""")
        files["report_commands.tcl"] = ("Reports to feed back into this tool", """# Vivado Tcl, after implementation (template, not run)
report_utilization -file util.rpt
report_power       -file power.rpt      ;# use a SAIF from simulation for a trustworthy dynamic number
# Paste util.rpt / power.rpt into Hardware > Read a vendor report to refine the device profile.
# Measure real power with a shunt/meter on the board rail; Vivado power is an estimate.
""")
        steps = ["Run your HLS/RTL flow (`hls4ml_flow.py` is a starting point).",
                 "Export `report_utilization` and `report_power`; paste them into the app to refine the device.",
                 "Measure latency from your testbench or board and import it."]
    else:
        steps = ["Run your workload on the device, measure latency and power, and import them on the Verify tab."]

    files["verify_hint.sh"] = ("How to report measurements back", "#!/usr/bin/env bash\n" + _post_report(spec, cfg, key))
    return Eco(key, key, files, steps)
