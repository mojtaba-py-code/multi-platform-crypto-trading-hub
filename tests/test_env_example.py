"""``.env.example`` is the documented starting point — it has to actually work.

The README tells you to copy it to ``.env`` and run the app. If a value in it
cannot be parsed, the failure is a startup crash with a pydantic-settings error,
which is a miserable first five minutes for anyone trying the project. These
tests load the real file and every documented value shape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.config.settings import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _documented_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]  # strip trailing comments
        match = _LINE.match(line)
        if match and match.group(2):
            values[match.group(1)] = match.group(2).strip('"')
    return values


def test_env_example_exists_and_documents_the_key_settings():
    documented = _documented_values()
    for required in ("APP_ENV", "DATABASE_URL", "CORS_ORIGINS", "ALLOW_LIVE_TRADING"):
        assert required in documented, f"{required} is missing from .env.example"


def test_settings_load_from_the_shipped_env_example(monkeypatch):
    for key, value in _documented_values().items():
        monkeypatch.setenv(key, value)
    # Must not raise: every documented value has to be parseable as written.
    settings = Settings(_env_file=None)

    assert settings.cors_origins  # the comma-separated form was understood
    assert settings.allow_live_trading is False  # the shipped default is safe


def test_env_example_never_ships_a_real_secret():
    documented = _documented_values()
    assert documented.get("MASTER_ENCRYPTION_KEY", "") == ""
    assert documented.get("JWT_SECRET_KEY", "") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000,http://localhost:8000",
            ["http://localhost:3000", "http://localhost:8000"],
        ),
        # Humans put spaces after commas.
        ("https://a.example , https://b.example", ["https://a.example", "https://b.example"]),
        ('["https://a.example"]', ['["https://a.example"]']),
    ],
)
def test_cors_origins_accepts_the_documented_shapes(monkeypatch, raw, expected):
    """A plain comma-separated string is the documented form and must not raise.

    pydantic-settings would otherwise try to JSON-decode a ``list[str]`` field
    straight from the environment and fail before any validator sees it.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected
