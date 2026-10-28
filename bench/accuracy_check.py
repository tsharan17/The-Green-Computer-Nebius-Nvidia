"""Accuracy retention of a variant vs the FP32 model on YOUR validation data (classification).

    python accuracy_check.py --fp32 resnet50.onnx --variant resnet50.int8.onnx \
        --inputs val_inputs/ --labels val_labels.npy

--inputs is a folder of preprocessed .npy files (one sample each, same preprocessing as
training) sorted by name; --labels is an int array of the same length. Prints top-1 accuracy of
both models and the retention (variant / fp32) to enter as `accuracy_retention`.

Only top-1 classification is implemented. For detection models (mAP) run your own evaluation and
enter the resulting ratio manually on the Verify screen. STATUS: written, not yet run end to end.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort


def top1(model: Path, files: list[Path], labels: np.ndarray) -> float:
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    correct = 0
    for f, y in zip(files, labels, strict=True):
        x = np.load(f).astype(np.float32)
        if x.ndim == 3:
            x = x[None]
        correct += int(np.argmax(sess.run(None, {name: x})[0], axis=1)[0] == y)
    return correct / len(files)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp32", required=True, type=Path)
    ap.add_argument("--variant", required=True, type=Path)
    ap.add_argument("--inputs", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    a = ap.parse_args()
    files = sorted(a.inputs.glob("*.npy"))
    labels = np.load(a.labels)
    if len(files) != len(labels) or not files:
        raise SystemExit(f"{len(files)} inputs but {len(labels)} labels")
    base, var = top1(a.fp32, files, labels), top1(a.variant, files, labels)
    print(json.dumps({"fp32_top1": base, "variant_top1": var, "accuracy_retention": var / base}))


if __name__ == "__main__":
    main()
