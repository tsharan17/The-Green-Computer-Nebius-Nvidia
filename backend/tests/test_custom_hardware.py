import pytest
from pydantic import ValidationError

from app.data_loader import get_model
from app.engine.evaluator import evaluate
from app.engine.hwbuild import CustomHardwareSpec, build_custom
from app.engine.optimise import baseline_config, optimise
from app.engine.vendor_reports import parse_power_csv, parse_report
from app.schemas.project import Constraints, ProjectSpec, Workload
from tests.helpers import AuthedClient

MCU = CustomHardwareSpec(
    name="Test M7", style="mcu", core_mhz=480, int8_macs_per_cycle=2, flash_kb=2048,
    sram_kb=1024, run_power_mw=250, sleep_power_mw=5, from_datasheet=True,
)
FPGA = CustomHardwareSpec(
    name="Test FPGA", style="fpga", dsp_slices=1248, fabric_mhz=250, bram_mb=1.4, uram_mb=4.5,
    mem_bw_gbs=19.2, mem_gb=4, static_w=0.7, dynamic_w=4.0, battery_wh=20,
)


def _spec(model_id, device_id, **cons):
    return ProjectSpec(
        name="t", model_id=model_id, device_id=device_id,
        workload=Workload(fps=5, ambient_c=25.0), constraints=Constraints(**cons),
    )


def test_missing_fields_rejected():
    with pytest.raises(ValidationError):
        CustomHardwareSpec(name="x y", style="mcu", core_mhz=100)


def test_mcu_profile_shape():
    hw = build_custom(MCU, "m7")
    assert hw.kind == "mcu" and set(hw.peak_tflops) == {"int8"}
    assert hw.weights_mem_gb == pytest.approx(2048 * 1024 / 1e9)
    assert hw.sources["weights_mem_gb"].value == "vendor_spec"
    assert hw.sources["peak_tflops"].value == "assumed"
    assert baseline_config(hw, None).precision == "int8"  # int8-only device: no crash


def test_mcu_fits_tiny_model_but_not_mobilenet():
    hw = build_custom(MCU, "m7")
    cons = Constraints(max_power_w=1.0, min_accuracy=0.9)
    wl = Workload(fps=5, ambient_c=25.0)
    cfg = baseline_config(hw, None)
    small = evaluate(get_model("tiny-kws-dscnn"), hw, cfg, wl, cons)
    big = evaluate(get_model("mobilenet-v2"), hw, cfg, wl, cons)
    assert "weights" in small.metrics
    assert not any(c.status == "fail" for c in small.checks if "fit" in c.name)
    # 3.5M int8 weights = 3.5 MB > 2 MB flash
    assert any(c.status == "fail" and "weights" in c.name for c in big.checks)
    assert small.metrics["latency"].value < 50  # milliseconds, sanity not exactness


def test_fpga_onchip_residency_removes_weight_traffic():
    hw = build_custom(FPGA, "f")
    no_oc = hw.model_copy(update={"onchip_weights_mb": None})
    cons = Constraints(max_power_w=20.0)
    wl = Workload(fps=30, ambient_c=25.0)
    cfg = baseline_config(hw, None)
    m = get_model("tiny-vww-mobilenet")
    a = evaluate(m, hw, cfg, wl, cons)
    b = evaluate(m, no_oc, cfg, wl, cons)
    assert a.metrics["latency"].value <= b.metrics["latency"].value
    assert "battery_hours" in a.metrics and a.metrics["battery_hours"].value > 0


def test_search_runs_on_custom_hardware():
    hw = build_custom(FPGA, "f")
    res = optimise(_spec("tiny-vww-mobilenet", "f", max_power_w=20.0), get_model("tiny-vww-mobilenet"), hw)
    assert res.evaluated > 100 and res.verdict is not None


def test_vivado_power_parser():
    text = """
    | Total On-Chip Power (W)  | 4.700 |
    | Design Power Budget (W)  | Unspecified* |
    | Dynamic (W)              | 4.000 |
    | Device Static (W)        | 0.700 |
    | Junction Temperature (C) | 62.1  |
    | Effective ThetaJA (C/W)  | 2.4   |
    """
    r = parse_report(text)
    assert r["tool"] == "vivado_power"
    assert r["fields"]["dynamic_w"] == 4.0 and r["fields"]["static_w"] == 0.7
    assert r["fields"]["theta_ja_k_per_w"] == 2.4


def test_vivado_util_parser():
    text = """
    | Slice LUTs*    | 41200 |     0 |     0 | 230400 | 17.88 |
    | DSPs           |   980 |     0 |     0 |   1728 | 56.71 |
    | Block RAM Tile |   120 |     0 |     0 |    312 | 38.46 |
    | URAM           |    10 |     0 |     0 |     96 | 10.42 |
    """
    r = parse_report(text, "vivado_util")
    assert r["fields"]["dsp_slices"] == 1728
    assert r["found"]["dsp"]["used"] == 980
    assert r["fields"]["bram_mb"] == pytest.approx(312 * 36 * 1024 / 8 / 1e6, rel=1e-3)


def test_stedgeai_and_vela_parsers():
    st = parse_report("ST Edge AI Core\n weights (ro) : 29,084 B (28.40 KiB)\n activations (rw) : 12,288 B\n MACC: 2,700,000\n")
    assert st["found"]["weights_bytes"] == 29084 and st["found"]["macc"] == 2_700_000
    vela = parse_report("Vela\n Total SRAM used 123.50 KiB\n Total Off-chip Flash used 400.00 KiB\n NPU total cycles 1,250,000\n")
    assert vela["found"]["npu_cycles"] == 1_250_000
    with pytest.raises(ValueError):
        parse_report("hello world, nothing here at all")


def test_power_csv_units_and_levels():
    rows = ["time(s),current(mA),voltage(V)"]
    for i in range(100):
        rows.append(f"{i * 0.01},{80 if 30 <= i < 60 else 2},3.3")
    r = parse_power_csv("\n".join(rows))
    assert r["power_w_active"] == pytest.approx(0.264, rel=0.02)
    assert r["power_w_idle"] == pytest.approx(0.0066, rel=0.05)
    with pytest.raises(ValueError):
        parse_power_csv("time,current(A)\n0,1\n1,1\n2,1")  # current without voltage


def test_custom_api_roundtrip(client):
    body = MCU.model_dump(mode="json", exclude_none=True)
    pre = client.post("/api/v1/hardware/custom/preview", json=body)
    assert pre.status_code == 200, pre.text
    r = client.post("/api/v1/hardware/custom", json=body)
    assert r.status_code == 201, r.text
    dev = r.json()["id"]
    assert dev in {d["id"] for d in client.get("/api/v1/hardware/library").json()}
    assert client.delete(f"/api/v1/hardware/custom/{dev}").status_code == 200
    assert client.post("/api/v1/hardware/parse-report", json={"text": "nothing useful here at all"}).status_code == 422


@pytest.fixture
def client(tmp_path):

    from app.data_loader import clear_custom_devices
    from app.main import create_app

    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'c.db'}")) as c:
        yield c
    clear_custom_devices()


def test_every_device_gets_a_valid_recipe():
    """Each ecosystem's generated Python parses; scripts are non-empty; MCUs skip the ONNX step."""
    from app.data_loader import load_devices
    from app.engine.optimise import quick_space
    from app.engine.recipe import build_recipe

    for dev in load_devices().values():
        mid = "tiny-vww-mobilenet" if dev.kind in ("mcu", "fpga") else "mobilenet-v2"
        model = get_model(mid)
        spec = _spec(mid, dev.id, max_power_w=500.0, min_accuracy=0.3)
        res = optimise(spec, model, dev, configs=quick_space(model, dev, None))
        files, _ = build_recipe(spec, model, dev, res)
        assert "README.md" in files and "runtime_config.json" in files
        for name, f in files.items():
            assert f.content.strip(), (dev.id, name)
            if name.endswith(".py"):
                compile(f.content, f"{dev.id}/{name}", "exec")
        if dev.kind == "mcu":
            assert "optimise_model.py" not in files and "deploy_mcu.md" in files


def test_layers_endpoint(client):
    r = client.post("/api/v1/projects", json={
        "name": "p", "model_id": "yolov8n", "device_id": "raspberry-pi-5",
        "workload": {"fps": 5}, "constraints": {"max_power_w": 20}})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    out = client.post(f"/api/v1/projects/{pid}/layers")
    assert out.status_code == 200 and out.json()["has_graph"] is False
