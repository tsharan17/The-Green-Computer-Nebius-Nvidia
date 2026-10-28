"""Versioned system graphs per project: the current graph, saving, building from uploaded files,
version history, diffs and links to the latest execution trace."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_loader import get_device
from app.db.models import ArtifactRow, ExecutionTraceRow, SystemGraphRow
from app.engine import system_graph as sg
from app.schemas.project import ProjectOut
from app.schemas.system_graph import SystemGraph
from app.services import audit

# Artifact kinds whose stored validation report is a parsed design file the graph can use.
GRAPH_KINDS = {"kicad_schematic", "kicad_pcb", "verilog", "hls_report", "vendor_report"}
GRAPH_TOOLS = {"kicad_sch", "kicad_pcb", "verilog", "hls_report", "vivado_util", "vivado_power", "vivado_timing"}


class GraphError(ValueError):
    pass


def _row_view(r: SystemGraphRow, with_graph: bool = True) -> dict[str, Any]:
    out = {"id": r.id, "version": r.version, "device_id": r.device_id, "note": r.note,
           "source_artifacts": r.source_artifacts, "created_by": r.created_by,
           "created_at": r.created_at.isoformat() if r.created_at else None,
           "components": len(r.graph.get("components", [])), "connections": len(r.graph.get("connections", []))}  # fmt: skip
    if with_graph:
        out["graph"] = r.graph
    return out


def _latest(db: Session, ws: int, project_id: int) -> SystemGraphRow | None:
    return db.scalars(select(SystemGraphRow).where(SystemGraphRow.workspace_id == ws, SystemGraphRow.project_id == project_id)
                      .order_by(SystemGraphRow.version.desc()).limit(1)).first()  # fmt: skip


def base_for(project: ProjectOut, ws: int) -> SystemGraph:
    spec = project.spec
    hw = get_device(spec.device_id, ws)
    power = spec.baseline.power_mode_id if spec.baseline else None
    cooling = spec.cooling_id or (spec.baseline.cooling_id if spec.baseline else None)
    return sg.base_graph(hw, power, cooling)


def current(db: Session, ws: int, project: ProjectOut) -> dict[str, Any]:
    """The latest saved version, or (version 0, unsaved) the graph built from the device profile.
    A saved graph for a different device than the project now uses is reported as outdated."""
    row = _latest(db, ws, project.id)
    if row is None:
        g = base_for(project, ws)
        return {"version": 0, "saved": False, "outdated": False, "graph": g.model_dump(mode="json"),
                "links": trace_links(db, ws, project.id), "note": "Built from the device profile; not saved yet."}  # fmt: skip
    return {**_row_view(row), "saved": True, "outdated": row.device_id != project.spec.device_id,
            "links": trace_links(db, ws, project.id)}  # fmt: skip


def _save(db: Session, ws: int, project_id: int, g: SystemGraph, user: int | None, note: str, arts: list[int]) -> SystemGraphRow:
    errs = sg.validate(g)
    if errs:
        raise GraphError("; ".join(errs))
    nxt = (db.scalar(select(func.max(SystemGraphRow.version)).where(SystemGraphRow.project_id == project_id)) or 0) + 1
    row = SystemGraphRow(workspace_id=ws, project_id=project_id, version=nxt, device_id=g.device_id,
                         schema_id=g.schema_id, note=note[:1000], source_artifacts=arts,
                         graph=g.model_dump(mode="json"), created_by=user)  # fmt: skip
    db.add(row)
    db.flush()
    audit.audit(db, "system_graph.saved", workspace_id=ws, user_id=user, target_type="system_graph", target_id=row.id,
                detail={"project_id": project_id, "version": nxt, "artifacts": arts}, commit=False)  # fmt: skip
    db.commit()
    return row


def save(db: Session, ws: int, project: ProjectOut, g: SystemGraph, user: int | None, note: str) -> dict[str, Any]:
    if g.device_id != project.spec.device_id:
        raise GraphError(f"the graph is for {g.device_id}, but the project uses {project.spec.device_id}")
    return _row_view(_save(db, ws, project.id, g, user, note or "edited", []))


def eligible_artifacts(db: Session, ws: int, project_id: int | None = None) -> list[dict[str, Any]]:
    q = select(ArtifactRow).where(ArtifactRow.workspace_id == ws, ArtifactRow.deleted_at.is_(None),
                                  ArtifactRow.status == "valid", ArtifactRow.kind.in_(GRAPH_KINDS))  # fmt: skip
    out = []
    for a in db.scalars(q.order_by(ArtifactRow.id.desc()).limit(200)):
        tool = (a.validation or {}).get("tool")
        if tool in GRAPH_TOOLS and (project_id is None or a.project_id in (None, project_id)):
            out.append({"id": a.id, "filename": a.filename, "tool": tool, "kind": a.kind, "project_id": a.project_id,
                        "created_at": a.created_at.isoformat() if a.created_at else None})  # fmt: skip
    return out


def build_from_artifacts(db: Session, ws: int, project: ProjectOut, artifact_ids: list[int], user: int | None,
                         note: str = "") -> dict[str, Any]:  # fmt: skip
    row = _latest(db, ws, project.id)
    g = (SystemGraph.model_validate(row.graph) if row is not None and row.device_id == project.spec.device_id
         else base_for(project, ws))  # fmt: skip
    used: list[int] = []
    for aid in dict.fromkeys(artifact_ids):
        a = db.get(ArtifactRow, aid)
        if a is None or a.workspace_id != ws or a.deleted_at is not None:
            raise GraphError(f"file {aid} not found")
        rep = a.validation or {}
        if a.status != "valid" or rep.get("tool") not in GRAPH_TOOLS:
            raise GraphError(f"{a.filename} is not a parsed design file or report the graph can use")
        g = sg.merge_parsed(g, rep, a.id, a.filename)
        used.append(a.id)
    if not used:
        raise GraphError("choose at least one uploaded design file")
    names = ", ".join(s.filename or "" for s in g.sources if s.artifact_id in used)
    return _row_view(_save(db, ws, project.id, g, user, note or f"added {names}", used))


def versions(db: Session, ws: int, project_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(SystemGraphRow).where(SystemGraphRow.workspace_id == ws, SystemGraphRow.project_id == project_id)
                      .order_by(SystemGraphRow.version.desc()))  # fmt: skip
    return [_row_view(r, with_graph=False) for r in rows]


def get_version(db: Session, ws: int, project_id: int, version: int) -> SystemGraphRow | None:
    return db.scalars(select(SystemGraphRow).where(SystemGraphRow.workspace_id == ws, SystemGraphRow.project_id == project_id,
                                                   SystemGraphRow.version == version)).first()  # fmt: skip


def diff(db: Session, ws: int, project: ProjectOut, a: int, b: int) -> dict[str, Any]:
    def load(v: int) -> SystemGraph:
        if v == 0:
            return base_for(project, ws)
        r = get_version(db, ws, project.id, v)
        if r is None:
            raise GraphError(f"version {v} not found")
        return SystemGraph.model_validate(r.graph)

    return {"from": a, "to": b, **sg.diff(load(a), load(b))}


def trace_links(db: Session, ws: int, project_id: int) -> dict[str, Any] | None:
    """Per trace component: how many events touch it, their busy time and the component's heat,
    from the project's most recent trace (predicted or measured, as labelled)."""
    t = db.scalars(select(ExecutionTraceRow).where(ExecutionTraceRow.workspace_id == ws, ExecutionTraceRow.project_id == project_id)
                   .order_by(ExecutionTraceRow.id.desc()).limit(1)).first()  # fmt: skip
    if t is None:
        return None
    per: dict[str, dict[str, float]] = {}
    for e in t.trace.get("events", []):
        for c in e.get("components", []):
            d = per.setdefault(c, {"events": 0, "busy_ms": 0.0})
            d["events"] += 1
            d["busy_ms"] += float(e.get("duration_ms", 0.0))
    for c in t.trace.get("components", []):
        d = per.setdefault(c["id"], {"events": 0, "busy_ms": 0.0})
        d["heat_w"] = float(c.get("heat_w", 0.0))
        d["busy_ms"] = round(d["busy_ms"], 4)
    return {"trace_id": t.id, "kind": t.kind, "provenance": t.provenance, "label": t.label, "components": per}
