"""Blob storage for uploaded files and generated artifacts, behind a small interface.

``LocalBlobStore`` keeps files on disk under ``<data_dir>/blobs/<workspace>/<sha256>``: one copy per
workspace (so deleting one tenant's data never touches another's) and content-addressed within it
(re-uploading the same file stores it once). An S3-compatible store can implement the same three
methods.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"^(ws\d+|library)/[0-9a-f]{64}$")


class BlobStore(Protocol):
    name: str

    def put(self, namespace: str, data: bytes) -> tuple[str, str]: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def delete_namespace(self, namespace: str) -> None: ...
    def path(self, key: str) -> Path | None: ...


class LocalBlobStore:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("invalid blob key")
        return self.root / key

    def put(self, namespace: str, data: bytes) -> tuple[str, str]:
        """Store bytes; returns (key, sha256)."""
        if not re.fullmatch(r"ws\d+|library", namespace):
            raise ValueError("invalid namespace")
        digest = hashlib.sha256(data).hexdigest()
        key = f"{namespace}/{digest}"
        dest = self._file(key)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        return key, digest

    def get(self, key: str) -> bytes:
        return self._file(key).read_bytes()

    def path(self, key: str) -> Path | None:
        p = self._file(key)
        return p if p.is_file() else None

    def delete(self, key: str) -> None:
        p = self._file(key)
        if p.exists():
            p.unlink()

    def delete_namespace(self, namespace: str) -> None:
        if not re.fullmatch(r"ws\d+", namespace):
            raise ValueError("invalid namespace")
        shutil.rmtree(self.root / namespace, ignore_errors=True)

    def has_namespace(self, namespace: str) -> bool:
        if not re.fullmatch(r"ws\d+", namespace):
            raise ValueError("invalid namespace")
        d = self.root / namespace
        return d.exists() and any(d.rglob("*"))


def default_data_dir(db_url: str, configured: str | None) -> Path:
    if configured:
        return Path(configured)
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        return Path(db_url[len("sqlite:///") :]).resolve().parent / "gc_data"
    if db_url.startswith("sqlite"):
        return Path(tempfile.mkdtemp(prefix="gc_data_"))
    return Path("./gc_data").resolve()
