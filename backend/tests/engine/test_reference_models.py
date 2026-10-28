"""The ONNX parser against real published architectures.

bench/reference_models.py builds ResNet-50 and MobileNetV2 from their published definitions.
Parsing them must reproduce the published parameter and FLOP counts, which validates the
parser on graphs it was not hand-tuned for.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "bench"))
import reference_models as R  # noqa: E402

from app.engine.workload.onnx_parser import parse_onnx  # noqa: E402


@pytest.mark.parametrize(
    ("build", "params_m", "gflops"),
    [
        (R.resnet50, 25.6, 8.2),  # torchvision: 25.557M params, 4.09 GMACs
        (R.mobilenet_v2, 3.5, 0.6),  # torchvision: 3.505M params, 0.30 GMACs
    ],
)
def test_published_counts_are_reproduced(build, params_m, gflops):
    p = parse_onnx(build(materialize=False), "ref").profile
    assert p.params_m == pytest.approx(params_m, rel=0.02)
    assert p.gflops == pytest.approx(gflops, rel=0.03)
    assert p.fidelity == "graph" and p.layers


def test_reference_graphs_actually_run_in_onnxruntime():
    ort = pytest.importorskip("onnxruntime")
    import numpy as np

    sess = ort.InferenceSession(R.mobilenet_v2(), providers=["CPUExecutionProvider"])
    out = sess.run(None, {"input": np.zeros((2, 3, 224, 224), np.float32)})[0]
    assert out.shape == (2, 1000)
