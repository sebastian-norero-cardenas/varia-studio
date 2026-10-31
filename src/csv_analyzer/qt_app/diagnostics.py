"""Small, privacy-aware best-effort logging helpers for the Qt foundation."""

from __future__ import annotations

import logging
from typing import Any


def _safe_atom(value: Any) -> str:
    """Render only simple diagnostic atoms without invoking arbitrary repr()."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return type(value).__name__


def safe_error_fields(error: BaseException) -> dict[str, Any]:
    """Return structural exception data without messages, payloads or raw paths."""
    fields: dict[str, Any] = {"exception_class": type(error).__name__}
    cause = error.__cause__
    if cause is not None:
        fields["cause_class"] = type(cause).__name__

    traceback = error.__traceback__
    if traceback is not None:
        while traceback.tb_next is not None:
            traceback = traceback.tb_next
        fields["origin_function"] = traceback.tb_frame.f_code.co_name
        fields["origin_line"] = traceback.tb_lineno
    return fields


def best_effort_log(
    logger: logging.Logger,
    level: int,
    event: str,
    /,
    **fields: Any,
) -> None:
    """Log without ever allowing observability failure to affect application flow."""
    try:
        parts = [f"event={_safe_atom(event)}"]
        parts.extend(f"{key}={_safe_atom(value)}" for key, value in fields.items())
        logger.log(level, " ".join(parts))
    except Exception:
        # Logging is deliberately outside the functional correctness boundary.
        # Do not recursively log this failure.
        return
