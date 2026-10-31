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
    """Return structural exception data; diagnostic introspection is no-throw."""
    fields: dict[str, Any] = {}
    try:
        fields["exception_class"] = type(error).__name__
    except BaseException:
        fields["exception_class"] = "unavailable"

    try:
        cause = error.__cause__
    except BaseException:
        cause = None
    if cause is not None:
        try:
            fields["cause_class"] = type(cause).__name__
        except BaseException:
            pass

    try:
        traceback = error.__traceback__
    except BaseException:
        traceback = None
    if traceback is not None:
        try:
            while traceback.tb_next is not None:
                traceback = traceback.tb_next
            fields["origin_function"] = traceback.tb_frame.f_code.co_name
            fields["origin_line"] = traceback.tb_lineno
        except BaseException:
            pass
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
    except BaseException:
        # Observability is deliberately outside the functional correctness boundary.
        # Do not recursively log this failure.
        return


def best_effort_exception_log(
    logger: logging.Logger,
    level: int,
    event: str,
    error: BaseException,
    /,
    *,
    field_prefix: str = "",
    **fields: Any,
) -> None:
    """Prepare structural exception metadata and emit it inside one no-throw boundary."""
    try:
        merged = dict(fields)
        for key, value in safe_error_fields(error).items():
            merged[f"{field_prefix}{key}"] = value
        best_effort_log(logger, level, event, **merged)
    except BaseException:
        # This outer guard protects the caller even if metadata preparation is
        # changed later or an adversarial exception breaks introspection.
        return
