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
    dialog.setDefaultSuffix(default_suffix)
    dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        files = dialog.selectedFiles()
        return Path(files[0]).expanduser() if files else None
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
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
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
