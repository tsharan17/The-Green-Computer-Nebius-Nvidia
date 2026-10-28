"""Offline datasheet reading: text per page, scanned-page detection, candidate values.

No AI is needed. Candidates are found by pattern (compute TOPS/TFLOPS, memory size and bandwidth,
power, temperature limits) and always returned with the page and the line they came from, so a
person confirms each one before it enters a profile. If the optional AI provider is configured,
the existing extraction (agent/datasheet.py) can propose a full profile instead; either way
nothing is used unconfirmed.

OCR: only when the server has Tesseract (binary) plus the `pytesseract` and `pymupdf` packages.
Otherwise a scanned PDF is marked for manual review, never guessed.
"""

from __future__ import annotations

import io
import re
import shutil
from typing import Any

MIN_CHARS_PER_PAGE = 80  # below this a page is treated as an image scan
MAX_PAGES = 60

_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("peak_compute", "TOPS/TFLOPS", re.compile(
        r"(\d+(?:\.\d+)?)\s*(T(?:FL)?OPS|G(?:FL)?OPS|PFLOPS)\b(?:[^\n]{0,30}?\b(INT8|INT4|FP16|BF16|FP32|TF32)\b)?", re.I)),
    ("mem_bw_gbs", "GB/s", re.compile(r"(\d+(?:\.\d+)?)\s*(GB/s|GB/sec|GByte/s|TB/s)", re.I)),
    ("memory", "GB", re.compile(
        r"(\d+(?:\.\d+)?)\s*(GB|GiB|MB|MiB|KB|KiB)\b[^\n]{0,24}?\b(LPDDR\w*|DDR\w*|HBM\w*|RAM|SRAM|Flash|eMMC|memory)\b", re.I)),
    ("power_w", "W", re.compile(
        r"\b(TDP|power|consumption|typical|max(?:imum)?)\b[^\n]{0,30}?(\d+(?:\.\d+)?)\s*(W|mW)\b", re.I)),
    ("temperature_c", "°C", re.compile(
        r"\b(junction|operating|T[jJ]|shutdown|throttl\w*)\b[^\n]{0,40}?(-?\d+(?:\.\d+)?)\s*°?\s*C\b", re.I)),
]


def ocr_available() -> tuple[bool, str]:
    if shutil.which("tesseract") is None:
        return False, "Tesseract is not installed on the server (apt install tesseract-ocr)."
    try:
        import fitz  # noqa: F401  (pymupdf)
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "Install the Python packages: pip install pytesseract pymupdf."
    return True, "Tesseract OCR is available for scanned PDFs."


def _ocr_pages(data: bytes, pages: list[int]) -> dict[int, str]:  # pragma: no cover - needs Tesseract
    import fitz
    import pytesseract
    from PIL import Image

    out: dict[int, str] = {}
    doc = fitz.open(stream=data, filetype="pdf")
    for i in pages:
        pix = doc[i].get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        out[i] = pytesseract.image_to_string(img)
    return out


def read_pdf(data: bytes) -> dict[str, Any]:
    """{pages: [text...], scanned_pages: [...], ocr: {...}, errors: [...]}"""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return {"pages": [], "scanned_pages": [], "ocr": None,
                    "errors": ["The PDF is password-protected; upload an unprotected copy."]}  # fmt: skip
        texts = [(p.extract_text() or "") for p in reader.pages[:MAX_PAGES]]
        n_total = len(reader.pages)
    except Exception as e:  # noqa: BLE001 - any parse failure is a user-facing problem
        return {"pages": [], "scanned_pages": [], "ocr": None, "errors": [f"Could not read this PDF: {e}"]}
    scanned = [i for i, t in enumerate(texts) if len(t.strip()) < MIN_CHARS_PER_PAGE]
    ocr: dict[str, Any] | None = None
    if scanned:
        ok, why = ocr_available()
        ocr = {"available": ok, "detail": why, "pages_read": []}
        if ok:  # pragma: no cover - needs Tesseract
            try:
                for i, t in _ocr_pages(data, scanned).items():
                    texts[i] = t
                    ocr["pages_read"].append(i + 1)
            except Exception as e:  # noqa: BLE001
                ocr["detail"] = f"OCR failed: {e}"
    return {"pages": texts, "n_pages": n_total, "scanned_pages": [i + 1 for i in scanned], "ocr": ocr,
            "errors": [] if n_total <= MAX_PAGES else [f"Only the first {MAX_PAGES} of {n_total} pages were read."]}  # fmt: skip


def candidates(pages: list[str], per_field: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for pi, text in enumerate(pages):
        for line in text.splitlines():
            line_s = " ".join(line.split())
            if not line_s:
                continue
            for field, _unit, pat in _PATTERNS:
                for m in pat.finditer(line_s):
                    if counts.get(field, 0) >= per_field:
                        break
                    g = m.groups()
                    if field == "peak_compute":
                        value, u, prec = float(g[0]), g[1].upper(), (g[2] or "").lower() or None
                    elif field in ("mem_bw_gbs",):
                        value, u, prec = float(g[0]), g[1], None
                    elif field == "memory":
                        value, u, prec = float(g[0]), f"{g[1]} {g[2]}", None
                    elif field == "power_w":
                        value, u, prec = float(g[1]), g[2], g[0].lower()
                    else:
                        value, u, prec = float(g[1]), "°C", g[0].lower()
                    out.append({"field": field, "value": value, "unit": u, "qualifier": prec,
                                "page": pi + 1, "line": line_s[:200]})  # fmt: skip
                    counts[field] = counts.get(field, 0) + 1
    return out


def scan_datasheet(data: bytes) -> dict[str, Any]:
    r = read_pdf(data)
    pages = r["pages"]
    text_chars = sum(len(t.strip()) for t in pages)
    cands = candidates(pages)
    scanned = r["scanned_pages"]
    ocr = r["ocr"]
    unread = scanned and not (ocr and ocr.get("pages_read"))
    # A readable datasheet still needs a person to confirm its values, so it is never "valid".
    status = "invalid" if r["errors"] and not pages else "needs_review"
    return {
        "format": "pdf",
        "n_pages": r.get("n_pages", 0),
        "text_chars": text_chars,
        "scanned_pages": scanned,
        "ocr": ocr,
        "candidates": cands,
        "status": status,
        "errors": r["errors"],
        "manual_review": bool(unread) or not cands,
        "message": (
            "This looks like a scanned document and OCR is not available here, so nothing could be "
            "read. Enter the values by hand in the hardware form (the file is kept with the project)."
            if unread and text_chars < 200 else
            "Candidate values were found. Check each against the datasheet and confirm the ones that "
            "apply; nothing is used until you do." if cands else
            "No recognisable specifications were found in the text. Enter the values by hand."
        ),
    }
