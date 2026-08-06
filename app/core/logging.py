"""Structured logging setup.

Uses :mod:`structlog` to emit key/value logs — JSON in production, colourised
console output in development. A processor scrubs known-sensitive keys so secrets
never reach the logs even if a developer accidentally binds them.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# Keys whose values must never be written to logs, matched case-insensitively.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "api_secret",
        "api_key",
        "apikey",
        "passphrase",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "master_encryption_key",
        "jwt_secret_key",
        "private_key",
        "totp_secret",
    }
)

_REDACTED = "***redacted***"


def _redact_processor(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Replace values of sensitive keys with a placeholder."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and the stdlib logging bridge.

    Idempotent: safe to call more than once (tests call it repeatedly).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
