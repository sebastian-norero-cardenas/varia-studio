"""Canonical PySide6 frontend for Varia Studio."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Start the canonical Qt desktop application without eager Qt initialization."""
    from .bootstrap import main as bootstrap_main

    return bootstrap_main(argv)


__all__ = ["main"]
