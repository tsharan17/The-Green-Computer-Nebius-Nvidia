"""Remote device agents.

Workspace side (session or API token): register a device and get a one-time pairing code, list,
re-pair, revoke, approve a run of a validated ONNX file (a signed request), list runs.

Agent side (`/device-agent/...`, `Authorization: Device <token>`, never cookies): pair with the code,
heartbeat, fetch the next signed request, download only the approved file, return a signed result.
See docs/remote-agent.md for the protocol.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.db.models import RemoteDeviceRow
from app.remote import service as svc
from app.security import _limit

router = APIRouter(tags=["remote devices"])


def _secret(request: Request) -> bytes:
    return svc.signing_secret(request.app.state.data_dir)


def _fail(e: svc.RemoteError) -> HTTPException:
    headers = {"WWW-Authenticate": "Device"} if e.status == 401 else None
    return HTTPException(e.status, str(e), headers=headers)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    device_id: str = Field(min_length=1, max_length=120)


class JobIn(BaseModel):
    project_id: int
    artifact_id: int
    precision: str = "fp32"
    batch: int = 1
    warmup: int = 10
    runs: int = 100
    max_seconds: int = 120
    input_shape: list[int] | None = None


@router.post("/remote-devices", status_code=201)
def register_device(body: RegisterIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    """Register a device. The pairing code is shown once and works for 15 minutes."""
    try:
        return svc.register(db, ctx.workspace_id, ctx.user_id, body.name, body.device_id)
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.get("/remote-devices")
def list_devices(db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    return svc.list_devices(db, ctx.workspace_id)


@router.post("/remote-devices/{device_id}/pairing-code")
def repair(device_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    try:
        return svc.new_pairing_code(db, ctx.workspace_id, ctx.user_id, device_id)
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.delete("/remote-devices/{device_id}")
def revoke(device_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    try:
        return svc.revoke(db, ctx.workspace_id, ctx.user_id, device_id)
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.post("/remote-devices/{device_id}/jobs", status_code=201)
def approve_job(device_id: int, body: JobIn, request: Request, db: Session = Depends(get_db),
                ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:  # fmt: skip
    """Approve one run of a validated ONNX file on this device; the request is signed and expires in an hour."""
    p = project_for(db, ctx, body.project_id)
    params = body.model_dump(exclude={"project_id", "artifact_id"}, exclude_none=True)
    try:
        return svc.create_job(db, ctx.workspace_id, ctx.user_id, _secret(request), device_id, p, body.artifact_id, params)
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.get("/remote-jobs")
def list_jobs(device_id: int | None = None, project_id: int | None = None, db: Session = Depends(get_db),
              ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:  # fmt: skip
    if project_id is not None:
        project_for(db, ctx, project_id)
    return svc.list_jobs(db, ctx.workspace_id, device_id, project_id)


# --- agent side ---------------------------------------------------------------------------------


def require_device(request: Request, db: Session = Depends(get_db)) -> RemoteDeviceRow:
    authz = request.headers.get("authorization", "")
    scheme, _, token = authz.partition(" ")
    if scheme.lower() != "device" or not token.strip():
        raise HTTPException(401, "device token required", headers={"WWW-Authenticate": "Device"})
    try:
        return svc.device_for_token(db, token.strip())
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.post("/device-agent/pair")
def agent_pair(request: Request, body: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Exchange a one-time pairing code for a device token and signing key (shown to the agent once)."""
    _limit(request, "device-pair", 10, 600.0, "pairing attempts")
    code = (body or {}).get("code")
    if not isinstance(code, str) or not code.strip():
        raise HTTPException(401, "pairing code required", headers={"WWW-Authenticate": "Device"})
    info = (body or {}).get("agent") if isinstance((body or {}).get("agent"), dict) else {}
    try:
        return svc.pair(db, _secret(request), code, info or {})
    except svc.RemoteError as e:
        raise _fail(e) from e


@router.post("/device-agent/heartbeat")
def agent_heartbeat(body: dict[str, Any] | None = None, db: Session = Depends(get_db),
                    dev: RemoteDeviceRow = Depends(require_device)) -> dict[str, Any]:  # fmt: skip
    return svc.heartbeat(db, dev, (body or {}).get("agent") or {})


@router.get("/device-agent/jobs/next", response_model=None)
def agent_next(db: Session = Depends(get_db), dev: RemoteDeviceRow = Depends(require_device)) -> dict[str, Any] | Response:
    job = svc.next_job(db, dev)
    return job if job is not None else Response(status_code=204)


@router.get("/device-agent/jobs/{job_id}/artifact")
def agent_artifact(job_id: int, request: Request, db: Session = Depends(get_db),
                   dev: RemoteDeviceRow = Depends(require_device)) -> Response:  # fmt: skip
    try:
        data, name = svc.artifact_for(db, request.app.state.blobs, dev, job_id)
    except svc.RemoteError as e:
        raise _fail(e) from e
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.post("/device-agent/jobs/{job_id}/result")
def agent_result(job_id: int, request: Request, body: dict[str, Any] | None = None, db: Session = Depends(get_db),
                 dev: RemoteDeviceRow = Depends(require_device)) -> dict[str, Any]:  # fmt: skip
    body = body or {}
    result, sig = body.get("result"), body.get("signature")
    if not isinstance(result, dict) or not isinstance(sig, str):
        raise HTTPException(422, "send {result: {...}, signature: '<hex>'}")
    try:
        return svc.submit_result(db, _secret(request), dev, job_id, result, sig)
    except svc.RemoteError as e:
        raise _fail(e) from e
