"""What this installation can do right now, honestly: available, needs a credential, turned off,
unavailable, or planned. Drives the capability screen and every "can I do X here?" decision."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy.engine import Engine

from app import __version__
from app.db.migrate import current_revision, head_revision
from app.engine import assumptions as A
from app.engine.datasheet_scan import ocr_available
from app.providers.ai import ai_status
from app.providers.execution import backend_infos


def _row(group: str, cid: str, name: str, status: str, detail: str, enable: str = "",
         **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {"group": group, "id": cid, "name": name, "status": status, "detail": detail,
            "enable": enable, **extra}  # fmt: skip


def quantization_available() -> tuple[bool, str]:
    try:
        from onnxruntime.quantization import quantize_dynamic  # noqa: F401

        return True, "ONNX Runtime dynamic INT8 quantisation, validated by running both models."
    except Exception as e:  # noqa: BLE001
        return False, f"onnxruntime.quantization is not importable: {e}"


def capabilities(engine: Engine, email_sender: str, blob_store: str) -> list[dict[str, Any]]:
    out = [
        _row("Analysis", "engine", "Analytical engine", "available",
             f"Engine {A.ENGINE_VERSION} (app {__version__}): roofline latency, memory, power, "
             "leakage-temperature coupling, throttling, energy, cost, carbon, optimisation search. "
             "Runs offline; results are predictions with bands and provenance."),
        _row("Analysis", "twin", "Digital twin v1 (analytical trace)", "available",
             "Per-operator predicted execution trace, tensor lifetimes, memory timeline, energy "
             "attribution and system map. Labelled predicted; never presented as a measurement."),
    ]  # fmt: skip
    for b in backend_infos():
        out.append(_row("Execution", b.id, b.name, b.status, b.detail, b.enable,
                        location=b.location, measures=b.measures))  # fmt: skip
    ok, why = quantization_available()
    out.append(_row("Model transformation", "int8-dynamic", "INT8 weight quantisation (ONNX)",
                    "available" if ok else "unavailable", why,
                    "" if ok else "pip install onnxruntime"))  # fmt: skip
    out.append(_row("Model transformation", "fp16", "FP16 conversion, pruning, distillation", "planned",
                    "Not performed by the platform. The recipe export contains scripts to run them "
                    "yourself; the analysis models their effect as predictions."))  # fmt: skip
    ai = ai_status()
    out.append(_row("Assistant", "ai", "AI assistant (optional)",
                    "available" if ai["configured"] else "needs_credential",
                    ai["role"] + (f" Endpoint: {ai['base_url']}" if ai["configured"] else ""),
                    "" if ai["configured"] else "Set TOKEN_FACTORY_API_KEY (or any OpenAI-compatible "
                    "endpoint via TOKEN_FACTORY_BASE_URL) and restart."))  # fmt: skip
    tav = bool(os.environ.get("TAVILY_API_KEY"))
    out.append(_row("Intake", "datasheet-search", "Datasheet web search",
                    "available" if tav and ai["configured"] else "needs_credential",
                    "Finds a datasheet online and drafts a profile for you to confirm.",
                    "" if tav and ai["configured"] else "Needs TAVILY_API_KEY and the AI assistant."))  # fmt: skip
    out.append(_row("Intake", "datasheet-offline", "Datasheet reading (offline)", "available",
                    "Text PDFs: candidate values with the line they came from, for you to confirm."))  # fmt: skip
    ocr_ok, ocr_why = ocr_available()
    out.append(_row("Intake", "ocr", "OCR for scanned PDFs", "available" if ocr_ok else "unavailable",
                    ocr_why if ocr_ok else f"{ocr_why} Scanned PDFs go to manual review instead.",
                    "" if ocr_ok else "Install tesseract-ocr, then pip install pytesseract pymupdf."))  # fmt: skip
    for cid, name in (("elf", "Firmware footprint (ELF)"), ("vendor-reports", "Vivado / ST Edge AI / Vela reports"),
                      ("power-logs", "Power and telemetry logs")):  # fmt: skip
        out.append(_row("Intake", cid, name, "available", "Parsed on upload; see the formats table."))
    out.append(_row("Intake", "eda", "Schematic, PCB, RTL, netlist, assembly", "planned",
                    "No parser exists yet. Upload guidance names the reports and profiles that work today."))  # fmt: skip
    dialect = engine.dialect.name
    cur, head = current_revision(engine), head_revision()
    out.append(_row("Storage", "database", f"Database ({'PostgreSQL' if dialect.startswith('postgres') else dialect})",
                    "available" if cur == head else "unavailable",
                    f"Schema revision {cur} (latest {head})."
                    + (" SQLite is for local use; use PostgreSQL for a shared server." if dialect == "sqlite" else "")))  # fmt: skip
    out.append(_row("Storage", "files", "File storage", "available", f"{blob_store} blob store, separate per workspace."))
    out.append(_row("Accounts", "email", "Email delivery",
                    "available" if email_sender == "smtp" else "unavailable",
                    "SMTP configured." if email_sender == "smtp" else
                    "No mail server configured: password-reset messages go to the server log.",
                    "" if email_sender == "smtp" else "Set GC_SMTP_HOST (and user/password)."))  # fmt: skip
    return out
