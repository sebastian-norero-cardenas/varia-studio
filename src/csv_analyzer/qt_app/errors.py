"""Qt-facing error presentation without scientific or parsing logic."""

from __future__ import annotations

import logging

from ..data import CancelledError, DataError
from .diagnostics import best_effort_log, safe_error_fields

logger = logging.getLogger(__name__)


def user_error_text(error: BaseException) -> str:
    """Return a concise user-facing message with privacy-safe diagnostics."""
    if isinstance(error, CancelledError):
        return "Operation cancelled."
    if isinstance(error, (DataError, ValueError, OSError)):
        return str(error)

    best_effort_log(
        logger,
        logging.ERROR,
        "unexpected_qt_operation",
        **safe_error_fields(error),
    )
    return f"Unexpected error: {error}"
