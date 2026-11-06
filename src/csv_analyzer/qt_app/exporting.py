"""Focused Qt export dialogs with explicit overwrite confirmation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget

from ..exports import ExportConflictError

T = TypeVar("T")

_SAVE_DIALOG_NAME = "varia-export-save-dialog"
_OVERWRITE_DIALOG_NAME = "varia-export-overwrite-dialog"


def _suffix_for_name_filter(name_filter: str, default_suffix: str) -> str:
    """Return one simple '*.ext' suffix, falling back to the caller default."""
    start = name_filter.rfind("(*.")
    if start >= 0 and name_filter.endswith(")"):
        glob = name_filter[start + 1 : -1].strip()
        if glob.startswith("*.") and " " not in glob:
            candidate = glob[1:]
            if candidate[1:].isalnum():
                return candidate

    fallback = default_suffix.strip()
    if not fallback:
        return ""
    return fallback if fallback.startswith(".") else f".{fallback}"


def choose_save_path(
    parent: QWidget,
    *,
    title: str,
    initial_name: str,
    name_filters: tuple[str, ...],
    default_suffix: str,
) -> Path | None:
    """Run one parent-owned Qt save dialog without relying on implicit overwrite."""
    dialog = QFileDialog(parent, title)
    dialog.setObjectName(_SAVE_DIALOG_NAME)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setNameFilters(list(name_filters))
    dialog.selectFile(initial_name)
    dialog.setDefaultSuffix("")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        files = dialog.selectedFiles()
        if not files:
            return None
        selected = Path(files[0]).expanduser()
        if selected.suffix:
            return selected
        suffix = _suffix_for_name_filter(dialog.selectedNameFilter(), default_suffix)
        return selected.with_name(f"{selected.name}{suffix}") if suffix else selected
    finally:
        dialog.deleteLater()


def export_with_confirmation(
    parent: QWidget,
    path: str | Path,
    writer: Callable[[bool], T | None],
) -> T | None:
    """Try safe publication first and retry once only after explicit Qt consent."""
    try:
        return writer(False)
    except ExportConflictError:
        destination = Path(path).expanduser()
        box = QMessageBox(parent)
        box.setObjectName(_OVERWRITE_DIALOG_NAME)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Replace file?")
        box.setText(f"The destination now exists:\n{destination}")
        box.setInformativeText("Replace it with this export?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        try:
            answer = box.exec()
        finally:
            box.deleteLater()
        if answer != QMessageBox.StandardButton.Yes:
            return None
        return writer(True)


def close_export_dialogs(parent: QWidget) -> None:
    """Reject export dialogs owned by one view/panel during invalidation or close."""
    for dialog in parent.findChildren(QDialog):
        if dialog.objectName() in {_SAVE_DIALOG_NAME, _OVERWRITE_DIALOG_NAME}:
            try:
                dialog.reject()
            except RuntimeError:
                pass
