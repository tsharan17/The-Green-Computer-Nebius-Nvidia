"""A small, dependency-free PDF writer for the engineering report.

Renders the report's Markdown subset (headings, paragraphs, bullet and numbered lists, pipe tables,
bold markers stripped) onto A4 pages with the standard PDF fonts (Helvetica, Helvetica-Bold,
Courier for tables). Text is WinAnsi-encoded; characters outside it are transliterated (≥ -> >=).
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime

PAGE_W, PAGE_H = 595.28, 841.89  # A4 points
MARGIN = 54
BODY = 10.0
LEAD = 1.35

_SUBST = {"≥": ">=", "≤": "<=", "→": "->", "←": "<-", "−": "-", "₂": "2", "²": "2", "✓": "v", "✗": "x",
          "⚠": "!", "≈": "~", "Δ": "delta ", "η": "eta", "θ": "theta", " ": " ", " ": " "}  # fmt: skip
# Approximate Helvetica advance widths (1/1000 em) for wrapping; Courier is fixed at 600.
_W = {c: 556 for c in "abcdeghnopqu0123456789"} | {c: 500 for c in "ksvxyz"} | {c: 278 for c in "fijlrt ., "}
_W |= {c: 722 for c in "ABCDGHNOQRUw"} | {c: 667 for c in "EKPSVXY"} | {"m": 833, "M": 833, "W": 944}


def _clean(s: str) -> str:
    for a, b in _SUBST.items():
        s = s.replace(a, b)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
    return s.encode("cp1252", "replace").decode("cp1252")


def _esc(s: str) -> str:
    raw = s.encode("cp1252", "replace")
    out = []
    for b in raw:
        ch = chr(b)
        if ch in "()\\":
            out.append("\\" + ch)
        elif b < 32 or b > 126:
            out.append(f"\\{b:03o}")
        else:
            out.append(ch)
    return "".join(out)


def _width(s: str, size: float, mono: bool) -> float:
    if mono:
        return len(s) * 0.6 * size
    return sum(_W.get(c, 556) for c in s) / 1000.0 * size


def _wrap(text: str, size: float, width: float, mono: bool = False) -> list[str]:
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if _width(trial, size, mono) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class _Doc:
    def __init__(self, title: str) -> None:
        self.pages: list[list[str]] = []
        self.title = title
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def line(self, text: str, font: str, size: float, indent: float = 0.0, gap_before: float = 0.0) -> None:
        self.y -= gap_before
        if self.y - size * LEAD < MARGIN:
            self._new_page()
        self.y -= size * LEAD
        self.pages[-1].append(f"BT /{font} {size:g} Tf {MARGIN + indent:.1f} {self.y:.1f} Td ({_esc(text)}) Tj ET")

    def rule(self) -> None:
        self.y -= 4
        self.pages[-1].append(f"0.8 G {MARGIN} {self.y:.1f} m {PAGE_W - MARGIN} {self.y:.1f} l S 0 G")
        self.y -= 4

    def render(self) -> bytes:
        fonts = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier"}
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        font_ids = {k: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{v} /Encoding /WinAnsiEncoding >>".encode())
                    for k, v in fonts.items()}  # fmt: skip
        res = "<< /Font << " + " ".join(f"/{k} {i} 0 R" for k, i in font_ids.items()) + " >> >>"
        pages_id = len(objs) + 1
        objs.append(b"")  # placeholder for /Pages
        kids = []
        n = len(self.pages)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        for i, ops in enumerate(self.pages, 1):
            foot = (f"BT /F1 8 Tf {MARGIN} 30 Td ({_esc(_clean(self.title))} - generated {stamp} - "
                    f"page {i} of {n}) Tj ET")  # fmt: skip
            stream = zlib.compress(("\n".join([*ops, foot])).encode("latin-1"))
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
            kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                            f"/Resources {res} /Contents {cid} 0 R >>".encode()))  # fmt: skip
        objs[pages_id - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {n} >>".encode()
        info = add(f"<< /Title ({_esc(_clean(self.title))}) /Producer (The Green Computer) >>".encode())
        catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
        out += (f"trailer << /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()  # fmt: skip
        return bytes(out)


def markdown_to_pdf(md: str, title: str) -> bytes:
    doc = _Doc(title)
    width = PAGE_W - 2 * MARGIN
    table: list[list[str]] = []

    def flush_table() -> None:
        if not table:
            return
        rows = [r for r in table if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
        cols = max(len(r) for r in rows)
        widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(cols)]
        size = BODY - 1.5
        max_chars = int(width / (0.6 * size))
        while sum(widths) + 3 * cols > max_chars and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1
        for k, r in enumerate(rows):
            cells = [(r[i] if i < len(r) else "")[: widths[i]].ljust(widths[i]) for i in range(cols)]
            doc.line(" | ".join(cells), "F3", size, gap_before=2 if k == 0 else 0)
            if k == 0:
                doc.line("-+-".join("-" * w for w in widths), "F3", size)
        doc.y -= 4
        table.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            table.append([_clean(c.strip()) for c in line.strip("|").split("|")])
            continue
        flush_table()
        if not line.strip():
            doc.y -= BODY * 0.5
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            size = {1: 18, 2: 14, 3: 12}[level]
            for k, ln in enumerate(_wrap(_clean(m.group(2)), size, width)):
                doc.line(ln, "F2", size, gap_before=(10 if level < 3 else 6) if k == 0 else 0)
            if level == 1:
                doc.rule()
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            indent = 12 + 6 * (len(m.group(1)) // 2)
            mark = "-" if m.group(2) in "-*" else m.group(2)
            lines = _wrap(_clean(m.group(3)), BODY, width - indent - 12)
            for k, ln in enumerate(lines):
                doc.line(f"{mark} {ln}" if k == 0 else f"  {ln}", "F1", BODY, indent)
            continue
        for ln in _wrap(_clean(line.strip()), BODY, width):
            doc.line(ln, "F1", BODY)
    flush_table()
    return doc.render()
