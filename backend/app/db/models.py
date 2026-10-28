from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _ws_fk() -> Mapped[int | None]:
    """Owning workspace. NULL means shared library data, or data from before accounts existed."""
    return mapped_column(ForeignKey("workspaces.id"), index=True, nullable=True)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict] = mapped_column(JSON)
    workspace_id: Mapped[int | None] = _ws_fk()
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProjectVersionRow(Base):
    """Immutable snapshot of a project's inputs. Every change to the spec adds a version."""

    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunRow(Base):
    """Stored engine outputs (kind: evaluate | optimise | trace | plan) so results are reproducible."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    engine_version: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSON)
    # The project version that was analysed (NULL for runs stored before versioning existed).
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CustomModelRow(Base):
    """User-supplied models (ONNX upload or manual parameters), including parsed layers."""

    __tablename__ = "custom_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[dict] = mapped_column(JSON)
    workspace_id: Mapped[int | None] = _ws_fk()
    # The uploaded file this model was parsed from (needed to execute it for real).
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MeasurementRow(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(80), index=True)
    model_id: Mapped[str] = mapped_column(String(80), index=True)
    precision: Mapped[str] = mapped_column(String(10))
    batch: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    record: Mapped[dict] = mapped_column(JSON)
    # NULL = shared library measurement committed with the app (read-only for everyone).
    workspace_id: Mapped[int | None] = _ws_fk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CalibrationRow(Base):
    """Every refit is stored for audit; only accepted versions are ever applied."""

    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(80), index=True)
    precision: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[bool] = mapped_column(Boolean)
    calibration: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    # NULL = fitted on library measurements only; otherwise fitted for (and visible to) one workspace.
    workspace_id: Mapped[int | None] = _ws_fk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BenchmarkJobRow(Base):
    """A Nebius Serverless Job launched to measure real GPU behaviour."""

    __tablename__ = "benchmark_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(120))
    job_id: Mapped[str] = mapped_column(String(200))
    device_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(80))
    precision: Mapped[str] = mapped_column(String(10))
    batches: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20))
    ingested: Mapped[bool] = mapped_column(Boolean, default=False)
    measurement_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String(600), nullable=True)
    workspace_id: Mapped[int | None] = _ws_fk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentRunRow(Base):
    """Every agent turn with its tool calls and results, so a session can be replayed."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_message: Mapped[str] = mapped_column(String(4000))
    reply: Mapped[str] = mapped_column(String(8000))
    events: Mapped[list] = mapped_column(JSON)
    guardrail_ok: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HardwareDraftRow(Base):
    """A hardware profile extracted from a datasheet; unusable until the user confirms it."""

    __tablename__ = "hardware_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|confirmed|rejected
    profile: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    missing: Mapped[list] = mapped_column(JSON)
    device_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    workspace_id: Mapped[int | None] = _ws_fk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --- Accounts and tenancy -----------------------------------------------------------------


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Presentation preferences only (display density, onboarding progress); never data access.
    preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional TOTP: the seed is sealed with the server key (app.auth.secret_box), never stored plain.
    mfa_secret: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mfa_recovery: Mapped[list | None] = mapped_column(JSON, nullable=True)  # SHA-256 hashes, used ones removed


class WorkspaceRow(Base):
    """An organisation or a person's own space. Everything a user creates belongs to one."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    personal: Mapped[bool] = mapped_column(Boolean, default=True)
    # Holds data created before accounts existed; claimed by the first account on this database.
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Owner-set policies (data retention); never access control.
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | editor | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SessionRow(Base):
    """A sign-in (browser cookie) or an API token. Only a hash of the token is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="browser")  # browser | api
    label: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetRow(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactRow(Base):
    """An uploaded file: metadata and validation result here, bytes in the blob store."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # valid | invalid | needs_review | unsupported
    status: Mapped[str] = mapped_column(String(20))
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogRow(Base):
    """Append-only record of who did what: uploads, analyses, plans, exports, account events."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int | None] = _ws_fk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class UsageEventRow(Base):
    """Metered platform usage (analyses, execution seconds, storage, remote GPU seconds)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ExecutionTraceRow(Base):
    """A stored execution trace (immutable). `kind` predicted = analytical engine; measured = an
    execution backend or an imported log. The two are never mixed in one row."""

    __tablename__ = "execution_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("project_versions.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(12))  # predicted | measured
    provenance: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(120))
    schema_id: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(200), default="")
    config: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    trace: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DesignPlanRow(Base):
    """A saved selection of changes with its analysis. Immutable: saving again under the same
    name adds the next version."""

    __tablename__ = "design_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_version_id: Mapped[int | None] = mapped_column(ForeignKey("project_versions.id"), nullable=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(1000), default="")
    changes: Mapped[list] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExportRow(Base):
    """Every export, with what it is (generated and validated / generated / template)."""

    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("design_plans.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(30))  # generated_validated | generated | template
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExecutionJobRow(Base):
    """A real run of the project's model on an execution backend (local CPU, optional GPU)."""

    __tablename__ = "execution_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    backend: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # queued | running | succeeded | failed
    params: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    measurement_id: Mapped[int | None] = mapped_column(ForeignKey("measurements.id"), nullable=True)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("execution_traces.id"), nullable=True)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemGraphRow(Base):
    """A saved version of a project's hardware system graph (immutable; saving adds a version)."""

    __tablename__ = "system_graphs"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_system_graphs_project_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[str] = mapped_column(String(120))
    schema_id: Mapped[str] = mapped_column(String(60))
    note: Mapped[str] = mapped_column(String(1000), default="")
    source_artifacts: Mapped[list] = mapped_column(JSON, default=list)
    graph: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RemoteDeviceRow(Base):
    """A registered device agent. Secrets are stored only as hashes."""

    __tablename__ = "remote_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    device_id: Mapped[str] = mapped_column(String(120))  # hardware profile the results belong to
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paired | revoked
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    pairing_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    agent_info: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemoteJobRow(Base):
    """A signed run request for one device and its result. Approved by the user who created it."""

    __tablename__ = "remote_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    remote_device_id: Mapped[int] = mapped_column(ForeignKey("remote_devices.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | claimed | succeeded | failed | rejected | expired
    payload: Mapped[dict] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    measurement_id: Mapped[int | None] = mapped_column(ForeignKey("measurements.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailVerificationRow(Base):
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RateLimitRow(Base):
    """Shared fixed-window counters behind the sliding-window rate limiter (all processes)."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class TermsAcceptanceRow(Base):
    """One acceptance of one version of a legal document (terms, privacy) by one account."""

    __tablename__ = "terms_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "document", "version", name="uq_terms_acceptance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(40))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
