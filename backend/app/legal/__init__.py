"""Terms, privacy and acceptable-use documents, versioned. Accepting records the version and time.

Bump a document's version when its meaning changes; users are asked to accept the new version at
their next sign-in. The text is a draft for professional legal review (it says so at the top).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

HERE = Path(__file__).parent
# id -> (title, version, file, needs acceptance)
DOCS: dict[str, tuple[str, str, str, bool]] = {
    "terms": ("Terms and Conditions", "2026-10-26", "terms.md", True),
    "privacy": ("Privacy Policy", "2026-10-26", "privacy.md", True),
    "acceptable-use": ("Acceptable Use Policy", "2026-10-26", "acceptable-use.md", False),
    "data": ("Your account and data", "2026-10-26", "data.md", False),
}  # fmt: skip
REQUIRED = tuple(k for k, v in DOCS.items() if v[3])


@cache
def text(doc: str) -> str:
    return (HERE / DOCS[doc][2]).read_text(encoding="utf-8")


def current_versions() -> dict[str, str]:
    return {k: DOCS[k][1] for k in REQUIRED}
