"""Test helpers: build tiny ONNX models with known FLOP / parameter counts."""

from __future__ import annotations

import numpy as np
from onnx import TensorProto, helper, numpy_helper


def tiny_cnn_bytes(dynamic: bool = False, random_weights: bool = False) -> bytes:
    """Conv(3->4, 3x3, pad 1) -> Relu -> GlobalAveragePool -> Flatten -> Gemm(4->10) on 1x3x8x8.

    Hand-computed expectations (see test_onnx_parser):
      conv   : 6912 MACs, 108 weights, 448 activation elements
      relu   : 256 FLOPs, fused into the conv layer
      gap    : 128 MACs-equivalent (256 FLOPs), 260 activation elements
      gemm   : 40 MACs, 40 + 10 params, 14 activation elements
    """
    shape = ["N", 3, "H", "W"] if dynamic else [1, 3, 8, 8]
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, shape)
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 10] if dynamic else [1, 10])

    rng = np.random.default_rng(0)

    def w(shape):
        return rng.standard_normal(shape).astype(np.float32) if random_weights else np.zeros(shape, np.float32)

    inits = [
        numpy_helper.from_array(w((4, 3, 3, 3)), "conv_w"),
        numpy_helper.from_array(w((4, 10)), "fc_w"),
        numpy_helper.from_array(w((10,)), "fc_b"),
    ]
    nodes = [
        helper.make_node("Conv", ["x", "conv_w"], ["c"], pads=[1, 1, 1, 1], name="conv"),
        helper.make_node("Relu", ["c"], ["r"], name="relu"),
        helper.make_node("GlobalAveragePool", ["r"], ["g"], name="gap"),
        helper.make_node("Flatten", ["g"], ["f"], name="flat"),
        helper.make_node("Gemm", ["f", "fc_w", "fc_b"], ["y"], name="fc"),
    ]
    graph = helper.make_graph(nodes, "tiny", [x], [y], initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    return model.SerializeToString()


# --- authenticated API clients ------------------------------------------------------------

TEST_PASSWORD = "a long test password"

try:
    from fastapi.testclient import TestClient as _TestClient
except ImportError:  # pragma: no cover
    _TestClient = object  # type: ignore[assignment,misc]


class AuthedClient(_TestClient):  # type: ignore[misc,valid-type]
    """A TestClient that signs up (or signs in, if the account exists) when entered and sends the
    CSRF header on every request, like the web app does."""

    def __init__(self, app, email: str = "tester@example.com", **kw):
        super().__init__(app, **kw)
        self._email = email

    def __enter__(self):
        c = super().__enter__()
        c.headers["X-GC-CSRF"] = "1"
        body = {"email": self._email, "password": TEST_PASSWORD, "accept_terms": True}
        r = c.post("/api/v1/auth/signup", json=body)
        if r.status_code == 409:
            r = c.post("/api/v1/auth/signin", json=body)
        assert r.status_code in (200, 201), r.text
        c.me = r.json()
        return c
