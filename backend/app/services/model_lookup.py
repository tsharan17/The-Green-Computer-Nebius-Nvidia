from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_loader import get_model, load_models
from app.db import repository as repo
from app.schemas.model import ModelProfile


def resolve_model(db: Session, model_id: str, workspace_id: int | None = None) -> ModelProfile:
    """A built-in model, or one of this workspace's own uploads (never another tenant's)."""
    custom = repo.get_custom_model(db, model_id, workspace_id)
    if custom is not None:
        return custom
    if model_id in load_models():
        return get_model(model_id)
    raise KeyError(f"unknown model '{model_id}'")


def visible_models(db: Session, workspace_id: int | None = None) -> list[ModelProfile]:
    return [*load_models().values(), *repo.list_custom_models(db, workspace_id)]
