"""Make FP16 and INT8 variants of an FP32 ONNX model for benchmarking.

    python export_models.py --model models/resnet50.onnx --out variants/ [--calib-dir npy_inputs/]

STATUS: written but not yet run end to end. Check the outputs load in ONNX Runtime before
trusting a benchmark taken from them.

- fp16: onnxconverter-common float16 conversion (pip install onnxconverter-common).
- int8: ONNX Runtime static quantisation. With --calib-dir (a folder of .npy inputs shaped like the
  model input) the scales come from real data. Without it, random data is used: that is fine for
  LATENCY and POWER benchmarks but the model's ACCURACY is then meaningless.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def to_fp16(src: Path, dst: Path) -> None:
    import onnx
    from onnxconverter_common import float16

    model = onnx.load(str(src))
    onnx.save(float16.convert_float_to_float16(model, keep_io_types=True), str(dst))


def to_int8(src: Path, dst: Path, calib_dir: Path | None) -> None:
    import onnxruntime as ort
    from onnxruntime.quantization import CalibrationDataReader, QuantType, quantize_static

    sess = ort.InferenceSession(str(src), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = [1 if not isinstance(d, int) else d for d in inp.shape]

    class Reader(CalibrationDataReader):
        def __init__(self) -> None:
            if calib_dir:
                self.files = sorted(calib_dir.glob("*.npy"))
            else:
                self.files = []
            self.n = 0
            self.limit = len(self.files) if self.files else 16

        def get_next(self):
            if self.n >= self.limit:
                return None
            x = (
                np.load(self.files[self.n]).astype(np.float32)
                if self.files
                else np.random.rand(*shape).astype(np.float32)
            )
            self.n += 1
            return {inp.name: x.reshape(shape)}

    quantize_static(
        str(src), str(dst), Reader(), weight_type=QuantType.QInt8, activation_type=QuantType.QInt8
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--calib-dir", type=Path, default=None)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    stem = a.model.stem
    to_fp16(a.model, a.out / f"{stem}.fp16.onnx")
    to_int8(a.model, a.out / f"{stem}.int8.onnx", a.calib_dir)
    print(f"wrote variants to {a.out}")


if __name__ == "__main__":
    main()
