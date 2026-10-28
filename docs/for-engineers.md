# For hardware and software engineers

The Green Computer predicts how an AI model behaves on given hardware, then says what to change. Two ways in.

## Upload your files

The Workspace has one drop zone; the app works out what each file is:

| File | What happens |
|---|---|
| `.onnx` | Parsed into a model profile and selected (dynamic dimensions default to 224 if you did not set one) |
| `greencomputer.yaml` | Loads the whole setup: model, device (library id or an inline `custom:` block), workload, requirements |
| `.yaml` / `.json` hardware description (`name` + `style`) | Creates your hardware and selects it |
| `.yaml` / `.json` full device profile | Registers it as `custom-...` |
| `.rpt` / `.txt` Vivado, ST Edge AI, Vela report | Values go into *Describe your own hardware* |
| `.csv` power-meter capture | Active and idle power go into *Describe your own hardware* |
| `.pdf` datasheet | Text is extracted; the Green Agent reads the specs into a draft you must confirm (needs the agent key; off in the public demo) |

`.pt`, `.pkl`, `.tflite` and other formats are refused with an explanation. Nothing you upload is executed. The PDF-to-specs step was tested only up to the point of calling the agent (no live model was available).

## Hardware engineers (boards, MCUs, FPGAs, accelerators)

1. **Pick or describe your hardware.** The picker lists 22 devices by class (search "stm32", "npu", "fpga"). If yours is missing, *Describe your own hardware* offers four starting points:
   - **Board or SoC**: peak TOPS per precision, memory bandwidth and size, maximum power.
   - **Microcontroller**: core MHz, int8 MACs per cycle, flash, SRAM, run and sleep power. Weights are checked against flash, activations against SRAM.
   - **FPGA**: DSP slices, clock, packing, BRAM/URAM, DDR bandwidth, static and dynamic power. Resource figures and power can be pasted from Vivado reports.
   - **AI accelerator**: TOPS, on-chip model memory, link bandwidth, power. Models larger than the on-chip memory stream weights over the link and slow down (the Coral cliff).
2. **Paste vendor reports** (Vivado `report_power` / `report_utilization`, ST Edge AI `analyze`, Arm Vela summary) or a **power-meter CSV** (Joulescope, Otii, PPK2) to fill the fields. The same readers are on the command line: `gc parse report.txt`.
3. **Read the result.** Verdict, the cheapest certified fix, recommendations at four levels (AI model, how you run it, which hardware, the chip/board design with modelled what-ifs), battery life if you gave a battery, and a **recipe** for your ecosystem: TFLite + XNNPACK on Raspberry Pi, Hailo (`hailortcli`), `edgetpu_compiler`, OpenVINO, QNN / AI Hub, Core ML + `powermetrics`, Ryzen AI, ST Edge AI / TFLM / Vela, hls4ml + Vivado reports, TensorRT + `nvidia-smi -pl`.
4. **Measure and feed back.** Real latency and power go in on the *Real measurements* page; calibration is applied only if it predicts held-out measurements better.

What it does not know: dataflow, caches, interconnect, DMA/bus contention, radio and peripheral power, and how well *your* RTL or kernels use the silicon. Where a number is a guess it is labelled `assumed`.

## Software engineers (model optimisation)

- **Layer by layer** (Technical details): the slowest layers, compute- versus memory-bound, time by operator, and which layers look fragile for int8 (first/last, softmax, normalisation, depthwise). Upload an ONNX file for your own graph; ResNet-50 and MobileNetV2 use their reference graphs.
- **Quantisation sensitivity on your data**: `engine/layers.py` can generate `quant_sensitivity.py`, which fake-quantises one layer at a time and ranks layers by output change on *your* samples. Keep the top ones in fp16.
- **Library and CLI**: `gc init` writes a `greencomputer.yaml` (model, device, workload, requirements, the setup you run today, budgets). Then:

```bash
gc check greencomputer.yaml                 # exit 0 pass, 1 fail, 2 uncertain
gc check greencomputer.yaml --save base.json
gc check greencomputer.yaml --compare base.json --tolerance 5   # fail if energy regresses by more than 5%
gc analyse greencomputer.yaml               # all advice
gc layers  greencomputer.yaml               # where the time goes
gc devices
```

  Python: `from app.sdk import load_project, analyse, gate`. `docs/ci/green-check.yml` is a pull-request workflow template that runs the check and posts a comment.
- **Exporting**: PyTorch and TensorFlow models must be exported to ONNX first (`torch.onnx.export`, `tf2onnx`); `.pt` and `.pkl` files are refused on purpose.

## Honest limits (read before trusting a number)

- Everything is analytical unless a row says measured. The only real measurements so far are CPU latency on the developer's machine.
- New devices use vendor figures for memory and power modes where available; throughput is dense or derived; none was measured. Marketing NPU "TOPS" are not achievable by real software.
- The vendor report readers and the ecosystem scripts were written from public documentation and not run against real tool output or devices.
- The CI template and the CLI regression gate have not been run in a real repository.
