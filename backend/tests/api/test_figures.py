"""Matplotlib/seaborn figures drawn from stored traces: every figure renders, in both themes."""

from __future__ import annotations

import os
import pathlib

import pytest

from app.data_loader import clear_custom_devices
from app.main import create_app
from app.render.figures import FIGURES
from tests.helpers import AuthedClient

P = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'fg.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def test_every_figure_renders_in_both_themes(app):
    out = os.environ.get("GC_FIGURE_DIR")
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        assert c.post(f"{P}/projects/{pid}/optimise").status_code == 200
        for which in ("baseline", "best"):
            tid = c.post(f"{P}/projects/{pid}/traces?which={which}").json()["id"]
            for name in FIGURES:
                for theme in ("light", "dark"):
                    r = c.get(f"{P}/projects/{pid}/traces/{tid}/figures/{name}", params={"theme": theme})
                    if r.status_code == 422 and name == "tensor-lifetimes":  # aggregate traces carry no tensors
                        assert "no tensor lifetimes" in r.text
                        continue
                    assert r.status_code == 200, (name, theme, r.text)
                    assert r.content.startswith(PNG) and len(r.content) > 8000
                    if out:
                        pathlib.Path(out).mkdir(parents=True, exist_ok=True)
                        pathlib.Path(out, f"{which}-{name}-{theme}.png").write_bytes(r.content)
        assert c.get(f"{P}/projects/{pid}/traces/{tid}/figures/nope").status_code == 404
        assert c.get(f"{P}/projects/{pid}/traces/9999/figures/memory-timeline").status_code == 404
