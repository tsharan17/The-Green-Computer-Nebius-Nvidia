from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ENV_LOADED = False


def load_dotenv(paths: list[Path] | None = None) -> None:
    """Load KEY=VALUE lines from `.env` (repo root, then backend/) without overriding variables
    that are already set. Tiny on purpose: no dependency, no interpolation, no secrets logged."""
    global _ENV_LOADED
    if paths is None:
        if _ENV_LOADED:
            return
        _ENV_LOADED = True
        here = Path(__file__).resolve()
        paths = [here.parents[2] / ".env", here.parents[1] / ".env"]
    for candidate in paths:
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    db_url: str
    api_prefix: str = "/api/v1"
    # development | production. Development shows password-reset links in the API response
    # (there is no mail server); production never does.
    environment: str = "development"
    # Where uploaded files are kept (blob store). Default: next to a SQLite file, else ./gc_data.
    data_dir: str | None = None
    session_days: int = 14
    cookie_secure: bool = False
    # Base URL used in emails (password reset links).
    public_url: str = "http://localhost:8000"
    auto_migrate: bool = True
    # Extra browser origins allowed to call the API with a session cookie (the Vite dev server).
    allowed_origins: tuple[str, ...] = ()
    allow_signup: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def get_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    environment = (env("GC_ENV") or "development").strip().lower()
    origins = tuple(o.strip() for o in (env("GC_ALLOWED_ORIGINS") or "").split(",") if o.strip())
    if environment != "production":
        origins += ("http://localhost:5173", "http://127.0.0.1:5173")
    return Settings(
        db_url=env("GC_DB_URL", "sqlite:///./green_computer.db"),
        environment=environment,
        data_dir=env("GC_DATA_DIR") or None,
        session_days=int(env("GC_SESSION_DAYS") or 14),
        cookie_secure=_flag("GC_COOKIE_SECURE", environment == "production"),
        public_url=(env("GC_PUBLIC_URL") or "http://localhost:8000").rstrip("/"),
        auto_migrate=_flag("GC_AUTO_MIGRATE", True),
        allowed_origins=origins,
        allow_signup=_flag("GC_ALLOW_SIGNUP", True),
    )


# --- LLM provider (Nebius Token Factory by default; any OpenAI-compatible endpoint works) -----

# Verified against the Token Factory documentation.
DEFAULT_LLM_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# NOT verified: these come from third-party catalogues. The router checks them against the
# provider's own model list at run time and falls back to name matching, so a wrong ID here
# degrades gracefully instead of breaking.
DEFAULT_MODEL_HINTS = {
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
}


@dataclass(frozen=True)
class LLMSettings:
    api_key: str | None
    base_url: str
    ultra: str
    super_: str
    nano: str
    timeout_s: float
    max_tool_calls: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def get_llm_settings() -> LLMSettings:
    load_dotenv()
    env = os.environ.get
    return LLMSettings(
        api_key=env("TOKEN_FACTORY_API_KEY") or None,
        base_url=(env("TOKEN_FACTORY_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/") + "/",
        ultra=env("GC_MODEL_ULTRA") or DEFAULT_MODEL_HINTS["ultra"],
        super_=env("GC_MODEL_SUPER") or DEFAULT_MODEL_HINTS["super"],
        nano=env("GC_MODEL_NANO") or DEFAULT_MODEL_HINTS["nano"],
        timeout_s=float(env("GC_LLM_TIMEOUT_S") or 120),
        max_tool_calls=int(env("GC_AGENT_MAX_TOOL_CALLS") or 12),
    )
