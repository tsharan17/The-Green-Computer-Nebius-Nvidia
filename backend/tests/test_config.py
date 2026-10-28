import os

import app.config as config


def test_dotenv_loader_sets_missing_vars_only_and_ignores_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nTOKEN_FACTORY_API_KEY=\"abc\"\nGC_MODEL_ULTRA = x/y \nEMPTY=\nNOEQUALS\n"
        "GC_MODEL_SUPER=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GC_MODEL_SUPER", "from-shell")
    for k in ("TOKEN_FACTORY_API_KEY", "GC_MODEL_ULTRA", "EMPTY"):
        monkeypatch.delenv(k, raising=False)
    config.load_dotenv([env, tmp_path / "missing.env"])
    assert os.environ["TOKEN_FACTORY_API_KEY"] == "abc"  # quotes stripped
    assert os.environ["GC_MODEL_ULTRA"] == "x/y"  # whitespace trimmed
    assert os.environ["GC_MODEL_SUPER"] == "from-shell"  # the shell wins over the file
    assert "EMPTY" not in os.environ and "NOEQUALS" not in os.environ
    for k in ("TOKEN_FACTORY_API_KEY", "GC_MODEL_ULTRA"):
        os.environ.pop(k, None)


def test_llm_settings_defaults_and_overrides(monkeypatch):
    s = config.get_llm_settings()
    assert not s.configured and s.base_url == config.DEFAULT_LLM_BASE_URL and s.max_tool_calls == 12
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY", "k")
    monkeypatch.setenv("TOKEN_FACTORY_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("GC_AGENT_MAX_TOOL_CALLS", "5")
    s2 = config.get_llm_settings()
    assert s2.configured and s2.base_url == "https://example.test/v1/" and s2.max_tool_calls == 5


def test_postgres_connections_are_tested_before_use_because_managed_databases_pause():
    import pytest

    pytest.importorskip("psycopg")
    from app.db.session import make_engine

    pg = make_engine("postgresql+psycopg://u:p@localhost:5432/d")
    assert pg.pool._pre_ping is True and pg.pool._recycle == 300
    assert make_engine("sqlite://").pool._pre_ping is False
