"""Focused staged editor for Scatter appearance and visual sampling."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..data import DataError
from ..plots import ScatterSpec, validate_scatter


@dataclass(frozen=True)
class ScatterStyle:
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    color_label: str = ""
    marker: str = "o"
    size: float = 12.0
    opacity: float = 0.8
    excluded_opacity: float = 0.12
    cmap: str = "viridis"
    grid: bool = True
    sample_limit: int | None = None


def _finite_float(text: str, label: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise DataError(f"{label}: enter a finite number.") from error
    if not math.isfinite(value):
        raise DataError(f"{label}: enter a finite number.")
    return value


def _positive_int(text: str, label: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise DataError(f"{label}: enter a positive integer.") from error
    if value < 1:
        raise DataError(f"{label}: enter a positive integer.")
    return value


class ScatterStyleDialog(QDialog):
    style_applied = Signal(object)

    MARKERS = ("o", ".", "s", "^", "v", "D", "*", "x", "+")
    COLORMAPS = (
        "viridis",
        "plasma",
        "inferno",
        "magma",
        "cividis",
        "coolwarm",
        "RdBu_r",
        "turbo",
        "gray",
    )

    def __init__(self, style: ScatterStyle, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Figure appearance")
        self.setModal(True)
        self.resize(540, 610)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.title = QLineEdit(style.title, self)
        form.addRow("Title", self.title)
        self.x_label = QLineEdit(style.x_label, self)
        form.addRow("X label", self.x_label)
        self.y_label = QLineEdit(style.y_label, self)
        form.addRow("Y label", self.y_label)
        self.color_label = QLineEdit(style.color_label, self)
        form.addRow("Color label", self.color_label)

        self.marker = QComboBox(self)
        self.marker.addItems(self.MARKERS)
        self.marker.setCurrentText(style.marker)
        form.addRow("Marker", self.marker)

        self.size = QLineEdit(str(style.size), self)
        form.addRow("Marker area", self.size)
        self.opacity = QLineEdit(str(style.opacity), self)
        form.addRow("Selected opacity", self.opacity)
        self.excluded_opacity = QLineEdit(str(style.excluded_opacity), self)
        form.addRow("Excluded opacity", self.excluded_opacity)

        self.cmap = QComboBox(self)
        self.cmap.addItems(self.COLORMAPS)
        self.cmap.setCurrentText(style.cmap)
        form.addRow("Colormap", self.cmap)

        self.grid = QCheckBox("Grid lines", self)
        self.grid.setChecked(style.grid)
        form.addRow("", self.grid)

        self.sampling = QComboBox(self)
        self.sampling.addItems(("All points", "Limit each group"))
        self.sampling.setCurrentText(
            "Limit each group" if style.sample_limit is not None else "All points"
        )
        self.sampling.currentTextChanged.connect(self._sync_sampling)
        form.addRow("Display sampling", self.sampling)

        self.sample_limit = QLineEdit(str(style.sample_limit or 50000), self)
        form.addRow("Maximum points per group", self.sample_limit)
        self._sync_sampling()

        note = QLabel(
            "Sampling affects the displayed/exported figure only. Statistics and filters "
            "continue to use every analytically selected row.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply = QPushButton("Apply appearance", self)
        apply.clicked.connect(self._apply)
        actions.addWidget(apply)
        layout.addLayout(actions)

    def _sync_sampling(self) -> None:
        self.sample_limit.setEnabled(self.sampling.currentText() == "Limit each group")

    def build_style(self) -> ScatterStyle:
        sample_limit = (
            _positive_int(self.sample_limit.text(), "Point limit")
            if self.sampling.currentText() == "Limit each group"
            else None
        )
        style = ScatterStyle(
            title=self.title.text(),
            x_label=self.x_label.text(),
            y_label=self.y_label.text(),
            color_label=self.color_label.text(),
            marker=self.marker.currentText(),
            size=_finite_float(self.size.text(), "Marker area"),
            opacity=_finite_float(self.opacity.text(), "Selected opacity"),
            excluded_opacity=_finite_float(self.excluded_opacity.text(), "Excluded opacity"),
            cmap=self.cmap.currentText(),
            grid=self.grid.isChecked(),
            sample_limit=sample_limit,
        )
        validate_scatter(
            ScatterSpec(
                "x",
                "y",
                marker=style.marker,
                size=style.size,
                opacity=style.opacity,
                excluded_opacity=style.excluded_opacity,
                cmap=style.cmap,
                sample_limit=style.sample_limit,
            )
        )
        return style

    def _apply(self) -> None:
        try:
            style = self.build_style()
        except Exception as error:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Invalid appearance", str(error))
            return
        self.style_applied.emit(style)
        self.accept()
