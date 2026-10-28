"""Audit trail and usage metering. Both are append-only and scoped to a workspace."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ArtifactRow, AuditLogRow, ExportRow, UsageEventRow

# Usage kinds and their units. Quantities are always positive; storage is reported from the
# artifacts table (bytes in use now), not summed from events.
USAGE_UNITS = {
    "analysis": "count",
    "trace": "count",
    "plan_analysis": "count",
    "export": "count",
    "execution_seconds": "s",
    "remote_gpu_seconds": "s",
    "artifact_upload_bytes": "B",
    "ai_tokens": "tokens",
}


def audit(
    db: Session,
    action: str,
    *,
    workspace_id: int | None,
    user_id: int | None,
    target_type: str = "",
    target_id: str | int = "",
    detail: dict[str, Any] | None = None,
    ip: str = "",
    commit: bool = True,
) -> None:
    db.add(AuditLogRow(
        workspace_id=workspace_id, user_id=user_id, action=action[:60],
        target_type=target_type[:40], target_id=str(target_id)[:80], detail=detail or {},
        ip=ip[:64],
    ))  # fmt: skip
    if commit:
        db.commit()


def usage(
    db: Session,
    kind: str,
    quantity: float,
    *,
    workspace_id: int,
    user_id: int | None,
    project_id: int | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    unit = USAGE_UNITS[kind]
    db.add(UsageEventRow(
        workspace_id=workspace_id, user_id=user_id, project_id=project_id, kind=kind,
        quantity=float(quantity), unit=unit, detail=detail or {},
    ))  # fmt: skip
    if commit:
        db.commit()


def audit_list(
    db: Session, workspace_id: int, limit: int = 200, project_id: int | None = None
) -> list[dict[str, Any]]:
    q = (
        select(AuditLogRow)
        .where(AuditLogRow.workspace_id == workspace_id)
        .order_by(AuditLogRow.id.desc())
        .limit(limit)
    )
    rows = list(db.scalars(q))
    if project_id is not None:
        rows = [r for r in rows if r.detail.get("project_id") == project_id
                or (r.target_type == "project" and r.target_id == str(project_id))]  # fmt: skip
    return [
        {"id": r.id, "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
         "detail": r.detail, "user_id": r.user_id, "created_at": r.created_at.isoformat()}
        for r in rows
    ]  # fmt: skip


def _rate(name: str) -> float:
    try:
        return float(os.environ.get(name) or 0.0)
    except ValueError:
        return 0.0


def usage_summary(db: Session, workspace_id: int, since: datetime | None = None) -> dict[str, Any]:
    """Totals per kind (all time and since `since`), storage in use, and platform cost.

    Platform cost uses operator-configured rates (GC_PRICE_*); a self-hosted install has none, so
    it reports zero and says so. Device electricity cost is NOT here: it is a property of each
    analysed design and is reported with the analysis, never mixed with platform charges.
    """
    since = since or datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def totals(after: datetime | None) -> dict[str, float]:
        q = select(UsageEventRow.kind, func.sum(UsageEventRow.quantity)).where(
            UsageEventRow.workspace_id == workspace_id
        )
        if after is not None:
            q = q.where(UsageEventRow.created_at >= after)
        got = {k: float(v or 0.0) for k, v in db.execute(q.group_by(UsageEventRow.kind))}
        return {k: got.get(k, 0.0) for k in USAGE_UNITS}

    storage = int(db.execute(
        select(func.coalesce(func.sum(ArtifactRow.size_bytes), 0)).where(
            ArtifactRow.workspace_id == workspace_id, ArtifactRow.deleted_at.is_(None),
            ArtifactRow.blob_key.is_not(None),
        )
    ).scalar_one())  # PostgreSQL returns SUM(bigint) as Decimal
    storage += int(db.execute(
        select(func.coalesce(func.sum(ExportRow.size_bytes), 0)).where(ExportRow.workspace_id == workspace_id)
    ).scalar_one())
    period = totals(since)
    rates: dict[str, float] = {
        "per_execution_minute": _rate("GC_PRICE_PER_EXECUTION_MINUTE"),
        "per_gpu_hour": _rate("GC_PRICE_PER_GPU_HOUR"),
        "per_gb_month": _rate("GC_PRICE_PER_GB_MONTH"),
        "per_analysis": _rate("GC_PRICE_PER_ANALYSIS"),
    }
    lines = {
        "analyses": (period["analysis"] + period["plan_analysis"]) * rates["per_analysis"],
        "local_execution": period["execution_seconds"] / 60.0 * rates["per_execution_minute"],
        "remote_gpu": period["remote_gpu_seconds"] / 3600.0 * rates["per_gpu_hour"],
        "storage": storage / 1e9 * rates["per_gb_month"],
    }
    configured = any(rates.values())
    return {
        "period_start": since.isoformat(),
        "this_period": period,
        "all_time": totals(None),
        "units": USAGE_UNITS,
        "storage_bytes_in_use": int(storage),
        "platform_cost": {
            "rates": rates,
            "currency": os.environ.get("GC_PRICE_CURRENCY") or "USD",
            "lines": lines,
            "total": sum(lines.values()),
            "configured": configured,
            "note": (
                "Platform charges at the operator's configured rates."
                if configured
                else "No platform rates are configured (self-hosted): platform cost is zero. "
                "Remote GPU jobs are billed by your cloud provider directly."
            ),
        },
        "device_energy_note": (
            "Electricity cost of running your design on the device is an engineering estimate shown "
            "with each analysis (cost per day); it is not a platform charge."
        ),
    }
