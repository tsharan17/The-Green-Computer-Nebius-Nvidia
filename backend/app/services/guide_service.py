"""The guided layer: use-case presets, demo projects, project health and global search.

Presentation helpers only. Every number they return was computed by the engine and stored with a
run; nothing here estimates anything itself.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_loader import load_devices, load_models
from app.db import repository as repo
from app.db.models import DesignPlanRow, ExecutionTraceRow, RunRow
from app.schemas.hardware import HardwareProfile
from app.schemas.project import Constraints, ProjectSpec, Workload

USE_CASES: list[dict[str, Any]] = [
    {"id": "edge-camera", "title": "Smart camera", "blurb": "Spot objects in a live video stream on a small edge computer.",
     "model_id": "yolov8n", "device_id": "jetson-orin-nano", "fps": 30, "ambient_c": 35, "max_power_w": 15,
     "max_latency_ms": 100, "priority": "balanced", "category": "board_soc"},
    {"id": "robot-vision", "title": "Robot vision", "blurb": "Detect objects for a mobile robot in a warm enclosure.",
     "model_id": "yolov8s", "device_id": "jetson-orin-nx", "fps": 30, "ambient_c": 40, "max_power_w": 55,
     "max_latency_ms": 100, "priority": "lower_heat", "category": "board_soc"},
    {"id": "mcu-voice", "title": "Voice command on a microcontroller", "blurb": "Recognise a few spoken keywords on a battery-powered MCU.",
     "model_id": "tiny-kws-dscnn", "device_id": "stm32h743", "fps": 4, "ambient_c": 30, "max_power_w": 0.5,
     "max_latency_ms": 250, "priority": "lower_power", "category": "mcu"},
    {"id": "fpga-inspection", "title": "Inspection on an FPGA", "blurb": "Detect people or defects at high frame rate on an FPGA board.",
     "model_id": "tiny-vww-mobilenet", "device_id": "xilinx-kria-kv260", "fps": 60, "ambient_c": 35, "max_power_w": 10,
     "max_latency_ms": 30, "priority": "faster", "category": "fpga"},
    {"id": "gpu-server", "title": "GPU inference server", "blurb": "Classify images at high throughput on a data-centre GPU.",
     "model_id": "resnet-50", "device_id": "nvidia-l4", "fps": 500, "ambient_c": 30, "max_power_w": 75,
     "max_latency_ms": 50, "priority": "lower_cost", "category": "accelerator"},
    {"id": "cloud-inference", "title": "Cloud detection service", "blurb": "Run a larger detector for many video streams in the cloud.",
     "model_id": "yolov8m", "device_id": "nebius-l40s", "fps": 300, "ambient_c": 25, "max_power_w": 350,
     "max_latency_ms": 100, "priority": "balanced", "category": "accelerator"},
]  # fmt: skip

PRIORITIES: list[dict[str, str]] = [
    {"id": "balanced", "title": "Balanced", "blurb": "Least energy per input, among setups that meet every requirement."},
    {"id": "faster", "title": "Faster response", "blurb": "Shortest time from input to answer."},
    {"id": "lower_power", "title": "Lower power", "blurb": "Lowest average power draw, e.g. for batteries or a power budget."},
    {"id": "lower_heat", "title": "Lower heat", "blurb": "Coolest-running setup, so it never slows itself down."},
    {"id": "lower_cost", "title": "Lower running cost", "blurb": "Lowest electricity cost per day (hardware price is not modelled)."},
    {"id": "fit_memory", "title": "Fit into memory", "blurb": "Smallest memory footprint, for tight devices."},
]  # fmt: skip


def spec_for(case: dict[str, Any], name: str | None = None, hw: HardwareProfile | None = None) -> ProjectSpec:
    hw = hw or load_devices()[case["device_id"]]
    return ProjectSpec(
        name=name or case["title"], model_id=case["model_id"], device_id=case["device_id"],
        cooling_id=hw.default_cooling, priority=case["priority"], use_case=case.get("id"),
        workload=Workload(fps=case["fps"], ambient_c=case["ambient_c"]),
        constraints=Constraints(max_power_w=case["max_power_w"], max_latency_ms=case["max_latency_ms"]),
    )  # fmt: skip


def health(db: Session, ws: int) -> list[dict[str, Any]]:
    """Per project: outcome today, outcome with the recommended setup, and the biggest bottleneck,
    from the latest stored analysis (or 'not analysed')."""
    out = []
    for p in repo.list_projects(db, ws, include_archived=True):
        row = repo.latest_run_row(db, p.id, "optimise")
        item: dict[str, Any] = {"project_id": p.id, "analysed": row is not None, "stale": False}
        if row is not None:
            r = row.result
            v = r.get("verdict") or {}
            item.update({
                "outcome": r["baseline"]["feasibility"],
                "best_outcome": (r.get("best") or {}).get("feasibility"),
                "bottleneck": r.get("limiting_factor") or r["baseline"].get("bottleneck"),
                "headline": (v.get("baseline") or {}).get("headline"),
                "energy_saving_percent": (v.get("savings") or {}).get("energy_percent"),
                "analysed_at": row.created_at.isoformat(),
            })  # fmt: skip
            vrow = repo.current_version_row(db, p.id)
            item["stale"] = bool(vrow and row.version_id and row.version_id != vrow.id)
        out.append(item)
    return out


def search(db: Session, ws: int, q: str, limit: int = 40) -> list[dict[str, Any]]:
    """Case-insensitive search over the workspace's own data and the shared library."""
    t = q.strip().lower()
    if len(t) < 2:
        return []
    hits: list[dict[str, Any]] = []

    def add(kind: str, title: str, sub: str, href: str) -> None:
        if len(hits) < limit:
            hits.append({"type": kind, "title": title, "subtitle": sub, "href": href})

    projects = repo.list_projects(db, ws, include_archived=True)
    pids = {p.id for p in projects}
    for p in projects:
        s = p.spec
        if t in f"{s.name} {s.model_id} {s.device_id}".lower():
            add("project", s.name, f"{s.model_id} on {s.device_id}, version {p.version}", f"#/p/{p.id}/summary")
    for pl in db.scalars(select(DesignPlanRow).where(DesignPlanRow.workspace_id == ws)):
        if t in pl.name.lower() or any(t in c["title"].lower() for c in pl.changes):
            add("plan", f"{pl.name} v{pl.version}", "design plan", f"#/p/{pl.project_id}/recommendations?tab=saved")
    for r in db.scalars(select(RunRow).where(RunRow.project_id.in_(pids), RunRow.kind == "optimise")
                        .order_by(RunRow.id.desc()).limit(200)):  # fmt: skip
        if t in f"run {r.id} {r.kind}".lower():
            add("run", f"Analysis {r.id}", f"project {r.project_id}", f"#/p/{r.project_id}/verify")
        for a in (r.result or {}).get("advice", []):
            if t in f"{a.get('title', '')} {a.get('action', '')}".lower():
                add("recommendation", a["title"], f"project {r.project_id}", f"#/p/{r.project_id}/recommendations")
    for d in load_devices(ws).values():
        if t in f"{d.name} {d.vendor} {d.kind} {d.id}".lower():
            add("hardware", d.name, f"{d.vendor}, {d.kind.replace('_', ' ')}", "#/")
    for m in load_models().values():
        if t in f"{m.name} {m.task} {m.id}".lower():
            add("model", m.name, m.task, "#/")
    for m in repo.list_custom_models(db, ws):
        if t in m.name.lower():
            add("model", m.name, "your upload", "#/")
    seen_tensor = 0
    for tr in db.scalars(select(ExecutionTraceRow).where(ExecutionTraceRow.workspace_id == ws)
                         .order_by(ExecutionTraceRow.id.desc()).limit(20)):  # fmt: skip
        for c in tr.trace.get("components", []):
            if t in c["name"].lower():
                add("component", c["name"], f"{tr.trace.get('device_name')}, trace {tr.id}", f"#/p/{tr.project_id}/system-map")
        for x in tr.trace.get("tensors", []):
            if seen_tensor < 10 and t in x["id"].lower():
                seen_tensor += 1
                add("tensor", x["id"], f"{x['bytes'] / 1e6:.2f} MB, trace {tr.id}", f"#/p/{tr.project_id}/memory")
    # De-duplicate (same title and link).
    uniq: dict[tuple[str, str], dict[str, Any]] = {}
    for h in hits:
        uniq.setdefault((h["title"], h["href"]), h)
    return list(uniq.values())
