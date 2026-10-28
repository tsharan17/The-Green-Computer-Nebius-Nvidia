"""Load the built-in device and model libraries from YAML (kept out of the pure engine)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile

DATA_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def _builtin_devices() -> dict[str, HardwareProfile]:
    out: dict[str, HardwareProfile] = {}
    for f in sorted((DATA_DIR / "devices").glob("*.yaml")):
        d = HardwareProfile.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        out[d.id] = d
    return out


# Devices users described, imported from a datasheet and confirmed, or uploaded as a profile
# (persisted in the database and re-registered at start-up), with the workspace that owns each.
# Ids are globally unique, so an id never resolves to another tenant's device.
_CUSTOM_DEVICES: dict[str, HardwareProfile] = {}
_OWNER: dict[str, int | None] = {}


def register_device(profile: HardwareProfile, owner: int | None = None) -> None:
    _CUSTOM_DEVICES[profile.id] = profile
    _OWNER[profile.id] = owner


def unregister_device(device_id: str) -> None:
    _CUSTOM_DEVICES.pop(device_id, None)
    _OWNER.pop(device_id, None)


def clear_custom_devices() -> None:
    _CUSTOM_DEVICES.clear()
    _OWNER.clear()


def all_device_ids() -> set[str]:
    """Every id in use (built-in and every tenant's), for allocating new unique ids only."""
    return set(_builtin_devices()) | set(_CUSTOM_DEVICES)


def device_owner(device_id: str) -> int | None:
    return _OWNER.get(device_id)


def load_devices(workspace_id: int | None = None) -> dict[str, HardwareProfile]:
    """Built-in devices plus the custom devices of one workspace (None: devices with no owner,
    as used by the CLI, the SDK and single-user scripts)."""
    own = {k: v for k, v in _CUSTOM_DEVICES.items() if _OWNER.get(k) == workspace_id}
    return {**_builtin_devices(), **own}


@lru_cache(maxsize=1)
def load_models() -> dict[str, ModelProfile]:
    out: dict[str, ModelProfile] = {}
    for f in sorted((DATA_DIR / "models").glob("*.yaml")):
        m = ModelProfile.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        out[m.id] = m
    return out


def get_device(device_id: str, workspace_id: int | None = None) -> HardwareProfile:
    try:
        return load_devices(workspace_id)[device_id]
    except KeyError:
        raise KeyError(f"unknown device '{device_id}'") from None


def get_model(model_id: str) -> ModelProfile:
    try:
        return load_models()[model_id]
    except KeyError:
        raise KeyError(f"unknown model '{model_id}'") from None


@lru_cache(maxsize=1)
def load_cooling_catalogue():
    from app.engine.cooling_catalogue import CoolingEntry

    raw = yaml.safe_load((DATA_DIR / "cooling_catalogue.yaml").read_text(encoding="utf-8"))
    return [CoolingEntry.model_validate(e) for e in raw["entries"]]
