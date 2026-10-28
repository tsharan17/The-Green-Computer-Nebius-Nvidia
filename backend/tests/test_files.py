import json

import pytest
import yaml

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture
def client(tmp_path):
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'f.db'}")) as c:
        yield c
    clear_custom_devices()


def up(client, name, content, **q):
    body = content if isinstance(content, bytes) else content.encode()
    return client.post(f"{P}/files/inspect", params={"filename": name, **q}, content=body)


MCU = {"name": "Uploaded MCU", "style": "mcu", "core_mhz": 200, "int8_macs_per_cycle": 2,
       "flash_kb": 1024, "sram_kb": 256, "run_power_mw": 100}


def test_hardware_spec_yaml_and_json(client):
    r = up(client, "board.yaml", yaml.safe_dump(MCU))
    assert r.status_code == 200 and r.json()["kind"] == "hardware_spec"
    assert r.json()["preview"]["kind"] == "mcu"
    r = up(client, "board.json", json.dumps(MCU))
    assert r.json()["kind"] == "hardware_spec"
    bad = up(client, "b.yaml", yaml.safe_dump({"name": "x y", "style": "mcu", "core_mhz": 100}))
    assert bad.status_code == 422 and "hardware description" in bad.json()["detail"]


def test_project_file_with_library_ids_and_inline_hardware(client):
    doc = {"name": "from file", "model": "tiny-kws-dscnn", "device": "stm32h743",
           "workload": {"fps": 4}, "requirements": {"max_power_w": 0.5}}
    r = up(client, "greencomputer.yaml", yaml.safe_dump(doc))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "project" and out["spec"]["device_id"] == "stm32h743"
    assert out["spec"]["constraints"]["max_power_w"] == 0.5

    doc2 = {**doc, "device": None, "custom": MCU}
    doc2.pop("device")
    r = up(client, "greencomputer.yaml", yaml.safe_dump(doc2))
    assert r.status_code == 200, r.text
    dev = r.json()["hardware_created"]
    assert dev and r.json()["spec"]["device_id"] == dev
    assert dev in {d["id"] for d in client.get(f"{P}/hardware/library").json()}

    nomodel = up(client, "p.yaml", yaml.safe_dump({**doc, "model": "my-secret.onnx"}))
    assert nomodel.status_code == 422 and "Upload the ONNX" in nomodel.json()["detail"]


def test_full_profile_registers(client):
    from app.data_loader import get_device

    prof = get_device("nvidia-t4").model_dump(mode="json")
    prof["id"], prof["name"] = "my-t4", "My T4 variant"
    r = up(client, "dev.json", json.dumps(prof))
    assert r.json()["kind"] == "hardware_profile"
    reg = client.post(f"{P}/hardware/profile", json=r.json()["profile"])
    assert reg.status_code == 201 and reg.json()["id"] == "custom-my-t4"
    clash = client.post(f"{P}/hardware/profile", json={**prof, "id": "nvidia-t4"})
    assert clash.status_code == 409


def test_reports_and_power_csv(client):
    viv = "| Total On-Chip Power (W) | 4.7 |\n| Dynamic (W) | 4.0 |\n| Device Static (W) | 0.7 |\n"
    r = up(client, "power.rpt", viv)
    assert r.json()["kind"] == "report" and r.json()["fields"]["dynamic_w"] == 4.0
    csv = "time(s),current(mA),voltage(V)\n" + "\n".join(
        f"{i * 0.01},{80 if 30 <= i < 60 else 2},3.3" for i in range(100)
    )
    r = up(client, "capture.csv", csv)
    assert r.json()["kind"] == "power" and r.json()["power_w_active"] == pytest.approx(0.264, rel=0.03)
    assert up(client, "x.csv", "time,current(A)\n0,1\n1,1\n2,1").status_code == 422
    assert up(client, "notes.txt", "just some prose, nothing to see here").status_code == 422


def test_refusals_and_limits(client):
    assert up(client, "model.pt", b"\x80abc").status_code == 422
    assert up(client, "model.onnx", b"abc").status_code == 422
    assert up(client, "a.exe", b"abc").status_code == 422
    assert up(client, "empty.yaml", b"").status_code == 422
    assert up(client, "list.yaml", "- 1\n- 2").status_code == 422


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


def test_pdf_needs_text_and_agent(client):
    assert up(client, "scan.pdf", _pdf("hi")).status_code == 422  # too little text
    long = "Peak 26 TOPS int8, memory bandwidth 100 GB/s. " * 12
    r = up(client, "datasheet.pdf", _pdf(long))
    assert r.status_code == 503 and "Green Agent" in r.json()["detail"]  # no key in tests
    assert up(client, "broken.pdf", b"not a pdf at all").status_code == 422
