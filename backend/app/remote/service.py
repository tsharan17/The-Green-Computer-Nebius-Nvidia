"""Device agents: registration and pairing, signed run requests, claiming, results and import.

Nothing here runs anything: the server only issues signed requests and checks what comes back.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import token_hash
from app.data_loader import load_devices
from app.db.models import ArtifactRow, RemoteDeviceRow, RemoteJobRow
from app.engine.measurement_quality import assess
from app.remote import protocol as P
from app.schemas.measurement import MeasurementIn
from app.schemas.project import ProjectOut
from app.services import audit
from app.services import measurement_service as ms

PAIRING_MINUTES = 15
JOB_TTL_MINUTES = 60
ONLINE_SECONDS = 120


class RemoteError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    a = _aware(dt)
    return a.isoformat() if a is not None else None


def signing_secret(data_dir: Path) -> bytes:
    """The server's agent-signing secret: GC_AGENT_SIGNING_SECRET, else a random key created once in
    the data directory (never in source)."""
    env = os.environ.get("GC_AGENT_SIGNING_SECRET")
    if env:
        return env.encode("utf-8")
    path = data_dir / "agent_signing.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows permissions
            pass
    return path.read_bytes()


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _norm(code: str) -> str:
    return code.strip().upper().replace(" ", "")


# --- user side ---------------------------------------------------------------------------------


def device_view(r: RemoteDeviceRow) -> dict[str, Any]:
    seen = _aware(r.last_seen_at)
    online = bool(seen and (_now() - seen).total_seconds() <= ONLINE_SECONDS and r.status == "paired")
    return {"id": r.id, "name": r.name, "device_id": r.device_id, "status": r.status, "online": online,
            "last_seen_at": _iso(seen), "agent_info": r.agent_info or {},
            "key_version": r.key_version, "created_at": _iso(r.created_at),
            "pairing_expires_at": _iso(r.pairing_expires_at) if r.status == "pending" else None}  # fmt: skip


def register(db: Session, ws: int, user: int | None, name: str, device_id: str) -> dict[str, Any]:
    if device_id not in load_devices(ws):
        raise RemoteError(f"unknown hardware profile '{device_id}'")
    code = _code()
    row = RemoteDeviceRow(workspace_id=ws, name=name.strip()[:120] or "Device", device_id=device_id, status="pending",
                          pairing_hash=token_hash(_norm(code)), pairing_expires_at=_now() + timedelta(minutes=PAIRING_MINUTES),
                          created_by=user, agent_info={})  # fmt: skip
    db.add(row)
    db.flush()
    audit.audit(db, "remote_device.registered", workspace_id=ws, user_id=user, target_type="remote_device",
                target_id=row.id, detail={"name": row.name, "device_id": device_id}, commit=False)  # fmt: skip
    db.commit()
    return {**device_view(row), "pairing_code": code}


def get_device_row(db: Session, ws: int, rid: int) -> RemoteDeviceRow:
    r = db.get(RemoteDeviceRow, rid)
    if r is None or r.workspace_id != ws:
        raise RemoteError("device not found", 404)
    return r


def new_pairing_code(db: Session, ws: int, user: int | None, rid: int) -> dict[str, Any]:
    """Re-pair: a new code; the old token and signing key stop working when the agent pairs again."""
    r = get_device_row(db, ws, rid)
    if r.status == "revoked":
        raise RemoteError("this device was revoked; register it again", 409)
    code = _code()
    r.pairing_hash = token_hash(_norm(code))
    r.pairing_expires_at = _now() + timedelta(minutes=PAIRING_MINUTES)
    audit.audit(db, "remote_device.pairing_code", workspace_id=ws, user_id=user, target_type="remote_device", target_id=r.id, commit=False)
    db.commit()
    return {**device_view(r), "pairing_code": code}


def revoke(db: Session, ws: int, user: int | None, rid: int) -> dict[str, Any]:
    r = get_device_row(db, ws, rid)
    r.status, r.token_hash, r.pairing_hash, r.revoked_at = "revoked", None, None, _now()
    for j in db.scalars(select(RemoteJobRow).where(RemoteJobRow.remote_device_id == r.id, RemoteJobRow.status.in_(("queued", "claimed")))):
        j.status, j.error, j.finished_at = "failed", "the device was revoked", _now()
    audit.audit(db, "remote_device.revoked", workspace_id=ws, user_id=user, target_type="remote_device", target_id=r.id, commit=False)
    db.commit()
    return device_view(r)


def list_devices(db: Session, ws: int) -> list[dict[str, Any]]:
    return [device_view(r) for r in db.scalars(select(RemoteDeviceRow).where(RemoteDeviceRow.workspace_id == ws).order_by(RemoteDeviceRow.id.desc()))]


def job_view(j: RemoteJobRow) -> dict[str, Any]:
    status = j.status
    if status in ("queued", "claimed") and _aware(j.expires_at) < _now():  # type: ignore[operator]
        status = "expired"
    res = j.result or {}
    return {"id": j.id, "remote_device_id": j.remote_device_id, "project_id": j.project_id, "artifact_id": j.artifact_id,
            "status": status, "params": j.payload.get("params"), "artifact": j.payload.get("artifact"),
            "mode": res.get("mode"), "environment": res.get("environment"), "quality": j.quality,
            "measurement_id": j.measurement_id, "error": j.error, "approved_by": j.approved_by,
            "created_at": _iso(j.created_at),
            "expires_at": _iso(j.expires_at),
            "finished_at": _iso(j.finished_at)}  # fmt: skip


def create_job(db: Session, ws: int, user: int | None, secret: bytes, rid: int, project: ProjectOut,
               artifact_id: int, params: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    """A signed run request. Creating it is the approval: only an approved, validated ONNX file of this
    workspace can be named, and the request is bound to its SHA-256."""
    r = get_device_row(db, ws, rid)
    if r.status != "paired":
        raise RemoteError("pair the device before sending it work", 409)
    a = db.get(ArtifactRow, artifact_id)
    if a is None or a.workspace_id != ws or a.deleted_at is not None:
        raise RemoteError("file not found", 404)
    if a.kind != "onnx_model" or a.status != "valid" or a.blob_key is None:
        raise RemoteError("only a validated ONNX model stored in this workspace can be run on a device")
    errs = P.check_params(params)
    if errs:
        raise RemoteError("; ".join(errs))
    expires = _now() + timedelta(minutes=JOB_TTL_MINUTES)
    row = RemoteJobRow(workspace_id=ws, remote_device_id=r.id, project_id=project.id, artifact_id=a.id, status="queued",
                       payload={}, signature="", approved_by=user, expires_at=expires)  # fmt: skip
    db.add(row)
    db.flush()
    payload = {"protocol": P.PROTOCOL, "job_id": row.id, "remote_device_id": r.id, "action": "benchmark_onnx",
               "artifact": {"id": a.id, "sha256": a.sha256, "size_bytes": a.size_bytes, "filename": a.filename},
               "params": params, "nonce": secrets.token_hex(16), "issued_at": _now().isoformat(),
               "expires_at": expires.isoformat()}  # fmt: skip
    row.payload = payload
    row.signature = P.sign(P.device_key(secret, r.id, r.key_version), payload)
    audit.audit(db, "remote_job.approved", workspace_id=ws, user_id=user, target_type="remote_job", target_id=row.id,
                detail={"device": r.id, "artifact": a.id, "sha256": a.sha256, "params": params}, commit=False)  # fmt: skip
    db.commit()
    return job_view(row)


def list_jobs(db: Session, ws: int, rid: int | None = None, project_id: int | None = None) -> list[dict[str, Any]]:
    q = select(RemoteJobRow).where(RemoteJobRow.workspace_id == ws)
    if rid is not None:
        q = q.where(RemoteJobRow.remote_device_id == rid)
    if project_id is not None:
        q = q.where(RemoteJobRow.project_id == project_id)
    return [job_view(j) for j in db.scalars(q.order_by(RemoteJobRow.id.desc()).limit(200))]


# --- agent side --------------------------------------------------------------------------------


def pair(db: Session, secret: bytes, code: str, info: dict[str, Any]) -> dict[str, Any]:
    r = db.scalars(select(RemoteDeviceRow).where(RemoteDeviceRow.pairing_hash == token_hash(_norm(code)))).first()
    if r is None or r.status == "revoked" or (_aware(r.pairing_expires_at) or _now()) < _now():
        raise RemoteError("pairing code not valid or expired", 401)
    token = secrets.token_urlsafe(32)
    was_paired = r.status == "paired"
    r.key_version = (r.key_version or 1) + (1 if was_paired else 0)
    r.status, r.token_hash, r.pairing_hash, r.pairing_expires_at = "paired", token_hash(token), None, None
    r.agent_info = {k: str(v)[:200] for k, v in list((info or {}).items())[:20]}
    r.last_seen_at = _now()
    audit.audit(db, "remote_device.paired", workspace_id=r.workspace_id, user_id=None, target_type="remote_device",
                target_id=r.id, detail={"agent": r.agent_info, "key_version": r.key_version}, commit=False)  # fmt: skip
    db.commit()
    return {"protocol": P.PROTOCOL, "remote_device_id": r.id, "device_token": token, "key_version": r.key_version,
            "signing_key": P.device_key(secret, r.id, r.key_version).hex(), "hardware_profile": r.device_id,
            "server_time": _now().isoformat()}  # fmt: skip


def device_for_token(db: Session, token: str) -> RemoteDeviceRow:
    r = db.scalars(select(RemoteDeviceRow).where(RemoteDeviceRow.token_hash == token_hash(token))).first()
    if r is None or r.status != "paired":
        raise RemoteError("device token not valid", 401)
    r.last_seen_at = _now()
    return r


def heartbeat(db: Session, r: RemoteDeviceRow, info: dict[str, Any]) -> dict[str, Any]:
    if info:
        r.agent_info = {**(r.agent_info or {}), **{k: str(v)[:200] for k, v in list(info.items())[:20]}}
    db.commit()
    return {"ok": True, "server_time": _now().isoformat()}


def next_job(db: Session, r: RemoteDeviceRow) -> dict[str, Any] | None:
    now = _now()
    for j in db.scalars(select(RemoteJobRow).where(RemoteJobRow.remote_device_id == r.id, RemoteJobRow.status == "queued")
                        .order_by(RemoteJobRow.id)):  # fmt: skip
        if _aware(j.expires_at) < now:  # type: ignore[operator]
            j.status, j.error, j.finished_at = "expired", "not picked up before it expired", now
            continue
        j.status, j.claimed_at = "claimed", now
        db.commit()
        return {"job": j.payload, "signature": j.signature}
    db.commit()
    return None


def _claimed(db: Session, r: RemoteDeviceRow, job_id: int) -> RemoteJobRow:
    j = db.get(RemoteJobRow, job_id)
    if j is None or j.remote_device_id != r.id:
        raise RemoteError("job not found", 404)
    if j.status != "claimed":
        raise RemoteError(f"job is {j.status}", 409)
    return j


def artifact_for(db: Session, blobs: Any, r: RemoteDeviceRow, job_id: int) -> tuple[bytes, str]:
    j = _claimed(db, r, job_id)
    a = db.get(ArtifactRow, j.artifact_id)
    if a is None or a.blob_key is None or a.deleted_at is not None:
        raise RemoteError("the approved file is no longer stored", 410)
    db.commit()
    return blobs.get(a.blob_key), a.filename


def submit_result(db: Session, secret: bytes, r: RemoteDeviceRow, job_id: int, result: dict[str, Any], signature: str) -> dict[str, Any]:
    j = _claimed(db, r, job_id)
    if not P.verify(P.device_key(secret, r.id, r.key_version), result, signature):
        raise RemoteError("result signature does not verify", 401)
    errs = P.check_result(result, j.payload)
    if errs:
        j.status, j.error, j.finished_at = "failed", "; ".join(errs)[:1000], _now()
        db.commit()
        raise RemoteError("; ".join(errs))
    j.result = result
    j.finished_at = _now()
    if result["status"] != "succeeded":
        j.status, j.error = result["status"], str(result.get("error", ""))[:1000]
        db.commit()
        return job_view(j)
    q = assess(result["latencies_ms"], result.get("warmup_ms"), result.get("timer_resolution_ns"),
               result.get("temperatures_c"), result.get("wall_seconds"))  # fmt: skip
    j.quality, j.status = q, "succeeded"
    if result["mode"] == "measured" and q["verdict"] != "poor":
        j.measurement_id = _import(db, r, j, result, q)
    elif result["mode"] == "simulated":
        j.error = "simulated run: kept for testing the set-up, never imported as a measurement"
    else:
        j.error = "quality too poor to import as a measurement; see the checks"
    audit.audit(db, "remote_job.result", workspace_id=j.workspace_id, user_id=None, target_type="remote_job", target_id=j.id,
                detail={"mode": result["mode"], "verdict": q["verdict"], "measurement_id": j.measurement_id}, commit=False)  # fmt: skip
    db.commit()
    return job_view(j)


def _import(db: Session, r: RemoteDeviceRow, j: RemoteJobRow, result: dict[str, Any], q: dict[str, Any]) -> int | None:
    from app.db import repository as repo

    a = db.get(ArtifactRow, j.artifact_id)
    project = repo.get_project(db, j.project_id, j.workspace_id)
    model_id = (a.validation or {}).get("model_id") if a else None
    model_id = model_id or (project.spec.model_id if project else None)
    if model_id is None:
        return None
    p = j.payload["params"]
    s = q["stats"]
    temps = result.get("temperatures_c") or []
    power = result.get("power_w") or []
    env = result.get("environment") or {}
    m = MeasurementIn(
        device_id=r.device_id, model_id=model_id, precision=p["precision"], batch=p["batch"], source="remote_agent",
        hardware_label=f"{r.name} ({env.get('hostname', 'agent')})"[:120], latency_ms_mean=s["mean"], latency_ms_p50=s["p50"],
        latency_ms_p95=s["p95"], latency_ms_p99=s["p99"], n_runs=s["n"],
        temperature_c_max=max(temps) if temps else None, power_w_avg=(sum(power) / len(power)) if power else None,
        measured_at=_now(), external_id=f"remote-job-{j.id}",
        notes=f"Remote agent, onnxruntime {env.get('onnxruntime', '?')}; quality {q['verdict']}: "
              + "; ".join(c["detail"] for c in q["checks"] if c["status"] != "pass")[:400],
    )  # fmt: skip
    stored, _created, _reports = ms.ingest(db, [m], j.workspace_id)
    return stored[0].id if stored else None
