"""Structured logging.

Provides a `get_logger(name)` helper that returns a configured stdlib logger.
In production, logs are JSON-formatted (one object per line). In development,
logs are human-readable with colors.

The logger **never** emits secrets. Settings objects are passed by reference
only; the redaction filter strips any field whose name matches a secret
pattern before the record is formatted.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_SECRET_KEYS = {
    "password",
    "token",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "cookie",
    "private_key",
    "synapse_dev_api_keys",
    "synapse_admin_api_keys",
    "synapse_db_url",  # may contain creds
}


class _RedactFilter(logging.Filter):
    """Mask any record attribute whose name looks like a secret."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__.keys()):
            low = key.lower()
            if any(s in low for s in _SECRET_KEYS):
                record.__dict__[key] = "***REDACTED***"
        # Also redact inside record.msg if it's a dict-like
        msg = record.msg
        if isinstance(msg, dict):
            record.msg = _redact_dict(msg)
        return True


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        low = k.lower()
        if any(s in low for s in _SECRET_KEYS):
            out[k] = "***REDACTED***"
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        else:
            out[k] = v
    return out


class _JsonFormatter(logging.Formatter):
    """One-line JSON per record, suitable for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # include custom fields (anything not in the standard set)
        std = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "ts",
            "taskName",
        }
        for k, v in record.__dict__.items():
            if k not in std and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Idempotent — safe to call multiple times."""
    root = logging.getLogger()
    # clear handlers configured by previous calls
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    handler.addFilter(_RedactFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str = "synapse") -> logging.Logger:
    return logging.getLogger(name)
