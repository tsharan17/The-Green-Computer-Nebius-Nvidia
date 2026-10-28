"""Build reference ONNX architectures (ResNet-50, MobileNetV2) with random weights.

Latency, throughput, power and memory depend on the architecture, not on trained weights, so
these graphs are valid benchmark workloads (they are NOT valid for accuracy measurements).
BatchNorm is folded away (convolutions carry a bias), as an inference exporter would do.

Only needs `onnx` and `numpy`. `materialize=False` emits initializers with shapes but no data,
which is enough to parse and count FLOPs but not to run.

    python reference_models.py --out models/        # writes resnet50.onnx, mobilenet_v2.onnx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from onnx import TensorProto, helper, numpy_helper


class _Builder:
    def __init__(self, materialize: bool, seed: int = 0):
        self.nodes: list = []
        self.inits: list = []
        self.n = 0
        self.materialize = materialize
        self.rng = np.random.default_rng(seed)

    def _name(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n}"

    def _weight(self, name: str, shape: tuple[int, ...], fan_in: int) -> str:
        if self.materialize:
            w = self.rng.normal(0.0, np.sqrt(2.0 / fan_in), shape).astype(np.float32)
            self.inits.append(numpy_helper.from_array(w, name))
        else:
            t = TensorProto()
            t.name = name
            t.data_type = TensorProto.FLOAT
            t.dims.extend(shape)
            self.inits.append(t)
        return name

    def conv(self, x: str, cin: int, cout: int, k: int, stride: int = 1, groups: int = 1) -> str:
        w = self._weight(self._name("w"), (cout, cin // groups, k, k), (cin // groups) * k * k)
        b = self._weight(self._name("b"), (cout,), cout)
        out = self._name("conv")
        self.nodes.append(
            helper.make_node(
                "Conv", [x, w, b], [out], kernel_shape=[k, k], strides=[stride, stride],
                pads=[k // 2] * 4, group=groups, name=out,
            )  # fmt: skip
        )
        return out

    def act(self, x: str, kind: str = "Relu") -> str:
        out = self._name(kind.lower())
        if kind == "Relu6":
            lo = numpy_helper.from_array(np.array(0.0, np.float32), out + "_lo")
            hi = numpy_helper.from_array(np.array(6.0, np.float32), out + "_hi")
            self.inits += [lo, hi]
            self.nodes.append(helper.make_node("Clip", [x, lo.name, hi.name], [out], name=out))
        else:
            self.nodes.append(helper.make_node("Relu", [x], [out], name=out))
        return out

    def add(self, a: str, b: str) -> str:
        out = self._name("add")
        self.nodes.append(helper.make_node("Add", [a, b], [out], name=out))
        return out

    def maxpool(self, x: str) -> str:
        out = self._name("pool")
        self.nodes.append(
            helper.make_node("MaxPool", [x], [out], kernel_shape=[3, 3], strides=[2, 2],
                             pads=[1, 1, 1, 1], name=out)  # fmt: skip
        )
        return out

    def head(self, x: str, features: int, classes: int = 1000) -> str:
        g = self._name("gap")
        self.nodes.append(helper.make_node("GlobalAveragePool", [x], [g], name=g))
        f = self._name("flat")
        self.nodes.append(helper.make_node("Flatten", [g], [f], axis=1, name=f))
        w = self._weight(self._name("fcw"), (classes, features), features)
        b = self._weight(self._name("fcb"), (classes,), classes)
        out = "logits"
        self.nodes.append(
            helper.make_node("Gemm", [f, w, b], [out], transB=1, name="fc")  # fmt: skip
        )
        return out

    def model(self, name: str, size: int, classes: int) -> bytes:
        x = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, size, size])
        y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", classes])
        graph = helper.make_graph(self.nodes, name, [x], [y], initializer=self.inits)
        m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
        m.ir_version = 8  # widely supported by runtimes
        return m.SerializeToString()


def resnet50(materialize: bool = True, size: int = 224) -> bytes:
    b = _Builder(materialize)
    x = b.act(b.conv("input", 3, 64, 7, stride=2))
    x = b.maxpool(x)
    cin = 64
    for planes, blocks, stride in ((64, 3, 1), (128, 4, 2), (256, 6, 2), (512, 3, 2)):
        for i in range(blocks):
            s = stride if i == 0 else 1
            y = b.act(b.conv(x, cin, planes, 1))
            y = b.act(b.conv(y, planes, planes, 3, stride=s))
            y = b.conv(y, planes, planes * 4, 1)
            sc = b.conv(x, cin, planes * 4, 1, stride=s) if (s != 1 or cin != planes * 4) else x
            x = b.act(b.add(y, sc))
            cin = planes * 4
    b.head(x, 2048)
    return b.model("resnet50", size, 1000)


def mobilenet_v2(materialize: bool = True, size: int = 224) -> bytes:
    b = _Builder(materialize)
    x = b.act(b.conv("input", 3, 32, 3, stride=2), "Relu6")
    cin = 32
    for t, c, n, s in (
        (1, 16, 1, 1), (6, 24, 2, 2), (6, 32, 3, 2), (6, 64, 4, 2),
        (6, 96, 3, 1), (6, 160, 3, 2), (6, 320, 1, 1),
    ):  # fmt: skip
        for i in range(n):
            stride = s if i == 0 else 1
            hidden = cin * t
            y = x
            if t != 1:
                y = b.act(b.conv(y, cin, hidden, 1), "Relu6")
            y = b.act(b.conv(y, hidden, hidden, 3, stride=stride, groups=hidden), "Relu6")
            y = b.conv(y, hidden, c, 1)
            x = b.add(x, y) if (stride == 1 and cin == c) else y
            cin = c
    x = b.act(b.conv(x, cin, 1280, 1), "Relu6")
    b.head(x, 1280)
    return b.model("mobilenet_v2", size, 1000)


BUILDERS = {"resnet-50": ("resnet50.onnx", resnet50), "mobilenet-v2": ("mobilenet_v2.onnx", mobilenet_v2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="models", type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for model_id, (fname, build) in BUILDERS.items():
        (args.out / fname).write_bytes(build())
        print(f"wrote {args.out / fname} ({model_id})")


if __name__ == "__main__":
    main()
