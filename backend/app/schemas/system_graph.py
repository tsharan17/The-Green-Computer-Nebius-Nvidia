"""Hardware system graph: the parts of a system, how they connect, and where each fact came from.

Versioned: `schema_id` = "green-computer.system-graph/1". Shared by the table view, the 2D map and a
3D view. Components that the analysis models carry `trace_component`, the id the execution trace
uses, so trace events link to graph parts. Positions come in two kinds and are never confused:
`logical` (a diagram arrangement, 0..1) and `physical` (millimetres from a board file).

Every component, connection and attribute names its `origin` (which file or profile stated it) and
a `confidence`:
  stated     written in a library profile or datasheet value
  parsed     read from an uploaded design file or report
  derived    computed from other facts here (e.g. utilisation = used / available)
  assumed    a default with no source
  measured   from a real run or a measurement log
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GRAPH_SCHEMA = "green-computer.system-graph/1"

Confidence = Literal["stated", "parsed", "derived", "assumed", "measured"]
ComponentKind = Literal[
    "system", "board", "compute", "memory", "storage", "link", "power", "cooling", "ic", "passive",
    "discrete", "connector", "clock", "logic_module", "sensor", "other",
]  # fmt: skip
ConnectionKind = Literal["memory_bus", "data", "power", "thermal", "clock", "net", "instance", "contains"]


class Attr(BaseModel):
    value: float | int | str | bool | None
    unit: str | None = None
    origin: str
    confidence: Confidence
    artifact_id: int | None = None
    note: str = ""


class LogicalLayout(BaseModel):
    """A diagram arrangement (0..1); says nothing about where the part physically sits."""

    x: float
    y: float
    w: float = 0.1
    h: float = 0.1
    z: float = 0.0


class PhysicalPlacement(BaseModel):
    """Where the part sits on the board, in millimetres, from a board file."""

    x_mm: float
    y_mm: float
    rotation_deg: float = 0.0
    side: Literal["top", "bottom"] = "top"
    width_mm: float | None = None
    height_mm: float | None = None


class GraphComponent(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(max_length=300)
    kind: ComponentKind
    parent: str | None = None
    attributes: dict[str, Attr] = {}
    origin: str
    confidence: Confidence
    artifact_id: int | None = None
    trace_component: str | None = None
    logical: LogicalLayout | None = None
    physical: PhysicalPlacement | None = None


class GraphConnection(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    source: str
    target: str
    kind: ConnectionKind
    label: str = ""
    attributes: dict[str, Attr] = {}
    origin: str
    confidence: Confidence
    artifact_id: int | None = None


class GraphSource(BaseModel):
    origin: str
    artifact_id: int | None = None
    filename: str | None = None
    components: int = 0
    connections: int = 0
    attributes: int = 0


class SystemGraph(BaseModel):
    schema_id: str = GRAPH_SCHEMA
    device_id: str
    device_name: str
    components: list[GraphComponent]
    connections: list[GraphConnection]
    sources: list[GraphSource] = []
    warnings: list[str] = []
    limitations: list[str] = []
