"""Guided intake: formats, profile validation with units, ELF footprint, datasheets, honest refusals."""

from __future__ import annotations

import struct

import pytest
import yaml

from app.data_loader import clear_custom_devices
from app.engine.datasheet_scan import candidates, scan_datasheet
from app.engine.elf_report import ElfError, check_against, elf_footprint
from app.engine.hw_validate import parse_quantity, template, validate_profile
from app.engine.intake_formats import CATEGORIES, unsupported_for
from app.main import create_app
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"
OCTET = {"Content-Type": "application/octet-stream"}


def make_elf32(sections: list[tuple[str, int, int, int]]) -> bytes:
    """ELF32 little-endian with (name, type, flags, size) sections plus a .shstrtab."""
    names = b"\x00" + b"".join(n.encode() + b"\x00" for n, *_ in sections) + b".shstrtab\x00"
    hdr_size, sh_size = 52, 40
    strtab_off = hdr_size
    shoff = strtab_off + len(names)
    shoff += (4 - shoff % 4) % 4
    shnum = len(sections) + 2  # null + sections + shstrtab
    ident = b"\x7fELF" + bytes([1, 1, 1]) + b"\x00" * 9
    header = ident + struct.pack("<HHIIIIIHHHHHH", 2, 0x28, 1, 0, 0, shoff, 0, hdr_size, 0, 0, sh_size,
                                 shnum, shnum - 1)  # fmt: skip
    body = header + names
    body += b"\x00" * (shoff - len(body))
    sh = struct.pack("<IIIIIIIIII", *([0] * 10))
    idx = 1
    for n, typ, flags, size in sections:
        sh += struct.pack("<IIIIIIIIII", idx, typ, flags, 0x08000000, 0, size, 0, 0, 4, 0)
        idx += len(n) + 1
    sh += struct.pack("<IIIIIIIIII", idx, 3, 0, 0, strtab_off, len(names), 0, 0, 1, 0)
    return body + sh


ALLOC, WRITE, EXEC, PROGBITS, NOBITS = 0x2, 0x1, 0x4, 1, 8
FIRMWARE = make_elf32([
    (".isr_vector", PROGBITS, ALLOC, 0x200),
    (".text", PROGBITS, ALLOC | EXEC, 120_000),
    (".rodata", PROGBITS, ALLOC, 30_000),
    (".data", PROGBITS, ALLOC | WRITE, 2_000),
    (".bss", NOBITS, ALLOC | WRITE, 40_000),
    (".comment", PROGBITS, 0, 50),
])  # fmt: skip


def make_pdf(lines: list[str]) -> bytes:
    """A minimal valid one-page PDF with the given text lines (or no text: an image-like page)."""
    content = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET" if lines else ""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets).encode()
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


DATASHEET = make_pdf([
    "Orinoco Edge Module datasheet",
    "AI performance: 40 TOPS INT8 sparse, 20 TOPS dense",
    "Memory: 8 GB LPDDR5, 102.4 GB/s bandwidth",
    "Typical power consumption: 15 W, maximum 25 W",
    "Junction temperature range up to 105 C",
] * 3)


@pytest.fixture()
def client(tmp_path):
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'i.db'}", data_dir=str(tmp_path / "d"))) as c:
        yield c
    clear_custom_devices()


# --- pure functions -----------------------------------------------------------------------


def test_every_category_lists_supported_and_honest_planned_formats():
    for cat in CATEGORIES:
        statuses = {f["status"] for f in cat["formats"]}
        assert "supported" in statuses
        for f in cat["formats"]:
            assert f["required"] and f["gives"]
    for name in ("board.brd", "top.vhd", "net.edf", "boot.s", "fw.hex", "model.pt", "m.tflite"):
        assert unsupported_for(name) is not None, name
    for name in ("model.onnx", "profile.yaml", "fw.elf", "util.rpt", "power.csv", "ds.pdf"):
        assert unsupported_for(name) is None, name


def test_quantities_with_units_are_converted_and_bad_units_refused():
    assert parse_quantity("68 GB/s", "mem_bw_gbs") == (68.0, None)
    v, note = parse_quantity("250 mW", "idle_w")
    assert v == pytest.approx(0.25) and "converted" in note
    assert parse_quantity("8 GiB", "mem_gb")[0] == pytest.approx(8.589934592)
    with pytest.raises(ValueError, match="not understood"):
        parse_quantity("5 furlongs", "mem_gb")


def test_template_validates_and_common_mistakes_are_caught():
    r = validate_profile(template())
    assert r["ok"], r["errors"]
    assert r["profile"]["mem_bw_gbs"] == 34 and r["profile"]["peak_tflops"]["int8"] == 9.6
    bad = template() | {"t_throttle_c": 110, "t_shutdown_c": 100, "mem_bw_gbs": "68 TB/s",
                        "default_cooling": "nope"}  # fmt: skip
    del bad["vendor"]
    r = validate_profile(bad)
    text = " ".join(r["errors"])
    assert not r["ok"] and "vendor" in text and "t_throttle_c" in text and "plausible" in text
    assert "default_cooling" in text
    assert validate_profile([1, 2])["ok"] is False


def test_elf_footprint_matches_the_section_table():
    fp = elf_footprint(FIRMWARE)
    assert fp["machine"] == "ARM (32-bit)"
    assert fp["flash_bytes"] == 0x200 + 120_000 + 30_000 + 2_000  # .data load image counts in flash
    assert fp["ram_bytes"] == 2_000 + 40_000
    assert all(s["name"] != ".comment" for s in fp["sections"])
    checks = check_against(fp, flash_kb=128, sram_kb=1024)
    assert {c["name"]: c["status"] for c in checks} == {"flash": "fail", "RAM": "pass"}
    with pytest.raises(ElfError, match="not an ELF"):
        elf_footprint(b":020000040800F2\n")


def test_datasheet_candidates_come_with_their_source_line():
    rep = scan_datasheet(DATASHEET)
    assert rep["status"] == "needs_review" and not rep["scanned_pages"]
    fields = {c["field"] for c in rep["candidates"]}
    assert {"peak_compute", "mem_bw_gbs", "memory", "power_w", "temperature_c"} <= fields
    top = next(c for c in rep["candidates"] if c["field"] == "peak_compute")
    assert top["value"] == 40 and top["qualifier"] == "int8" and "TOPS" in top["line"] and top["page"] == 1
    assert candidates(["nothing useful here"]) == []


def test_scanned_pdf_goes_to_manual_review_without_guessing():
    rep = scan_datasheet(make_pdf([]))
    assert rep["scanned_pages"] == [1] and rep["manual_review"] and rep["candidates"] == []
    assert rep["ocr"] is not None and "available" in rep["ocr"]


# --- API ----------------------------------------------------------------------------------


def test_formats_template_validation_and_capabilities_endpoints(client):
    cat = client.get(f"{P}/intake/formats").json()
    assert {c["id"] for c in cat["categories"]} == {"board_soc", "mcu", "fpga", "accelerator", "custom_system"}
    tpl = client.get(f"{P}/intake/templates/device-profile.yaml")
    assert tpl.status_code == 200 and validate_profile(yaml.safe_load(tpl.text))["ok"]
    assert client.post(f"{P}/intake/validate/device-profile", json={"name": "x"}).json()["ok"] is False
    caps = {c["id"]: c for c in client.get(f"{P}/capabilities").json()}
    assert caps["engine"]["status"] == "available" and caps["local-cpu"]["status"] == "available"
    assert caps["nebius-jobs"]["status"] in ("disabled", "needs_credential")
    assert caps["remote-device"]["status"] == "available"
    assert caps["ai"]["status"] == "needs_credential"
    assert caps["eda"]["status"] == "planned"
    assert caps["database"]["status"] == "available"


@pytest.mark.parametrize("fname", ["board.brd", "core.vhd", "design.edf", "main.s", "fw.hex", "m.pt"])
def test_unsupported_formats_get_an_honest_answer_and_are_recorded(client, fname):
    r = client.post(f"{P}/intake/upload?filename={fname}", content=b"whatever", headers=OCTET)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "unsupported" and body["required"]
    art = client.get(f"{P}/artifacts").json()[0]
    assert art["status"] == "unsupported" and not art["stored"]


def test_upload_routes_each_supported_kind(client):
    pid = client.post(f"{P}/projects", json={"name": "mcu", "model_id": "tiny-kws-dscnn",
                                             "device_id": "stm32h743"}).json()["id"]  # fmt: skip
    up = lambda name, data, extra="": client.post(  # noqa: E731
        f"{P}/intake/upload?filename={name}&project_id={pid}{extra}", content=data, headers=OCTET).json()

    r = up("tiny.onnx", tiny_cnn_bytes())
    assert r["status"] == "valid" and r["model"]["id"].startswith("custom-") and r["report"]["n_nodes"] == 5
    r = up("fw.elf", FIRMWARE)
    assert r["status"] == "valid" and r["report"]["checks"] and r["report"]["device"]
    r = up("ds.pdf", DATASHEET)
    assert r["status"] == "needs_review" and r["report"]["candidates"]
    r = up("board.yaml", yaml.safe_dump(template()).encode())
    assert r["status"] == "valid" and r["report"]["profile"]["id"] == "my-board"
    reg = client.post(f"{P}/hardware/profile", json=r["report"]["profile"])
    assert reg.status_code == 201 and reg.json()["id"].startswith("custom-")
    bad = up("board.json", b'{"peak_tflops": {"fp16": "3 furlongs"}, "power_modes": []}')
    assert bad["status"] == "invalid" and bad["report"]["errors"]
    r = up("power.csv", b"time_s,power_w\n0,1.0\n0.1,1.2\n0.2,1.4\n0.3,1.1\n", "")
    assert r["status"] == "valid"
    r = up("notes.txt", b"just some notes that are not a report")
    assert r["status"] == "invalid"
    dyn = up("dyn.onnx", tiny_cnn_bytes(dynamic=True))
    assert dyn["status"] == "invalid" and dyn["needs_dynamic_dim"]
    ok = up("dyn.onnx", tiny_cnn_bytes(dynamic=True), "&dynamic_dim_default=8")
    assert ok["status"] == "valid"
    kinds = {a["kind"] for a in client.get(f"{P}/artifacts?project_id={pid}").json()}
    assert {"onnx_model", "firmware_elf", "datasheet_pdf", "device_profile", "power_log"} <= kinds


def test_upload_size_limit(client):
    big = b"x" * (21 * 1024 * 1024)
    assert client.post(f"{P}/intake/upload?filename=log.csv", content=big, headers=OCTET).status_code == 413
