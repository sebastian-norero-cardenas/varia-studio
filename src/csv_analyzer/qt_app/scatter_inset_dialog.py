"""Focused staged editor for Scatter inset zoom."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..plots import AxisSpec, InsetSpec, ScatterSpec, validate_scatter
from .scatter_style_dialog import _finite_float


class ScatterInsetDialog(QDialog):
    inset_applied = Signal(object)

    LOCATIONS = ("upper right", "upper left", "lower right", "lower left")
    SCALES = ("inherit", "linear", "log")

    def __init__(
        self,
        inset: InsetSpec | None,
        *,
        x_scale: str = "linear",
        y_scale: str = "linear",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Inset zoom")
        self.setModal(True)
        self.resize(520, 560)
        self._main_x_scale = x_scale
        self._main_y_scale = y_scale

        base = asdict(inset or InsetSpec(0.1, 1.0, 0.1, 1.0))

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.enabled = QComboBox(self)
        self.enabled.addItems(("Off", "On"))
        self.enabled.setCurrentText("On" if inset is not None else "Off")
        self.enabled.currentTextChanged.connect(self._sync_enabled)
        form.addRow("Inset", self.enabled)

        self.x_min = QLineEdit(str(base["x_min"]), self)
        form.addRow("X min", self.x_min)
        self.x_max = QLineEdit(str(base["x_max"]), self)
        form.addRow("X max", self.x_max)
        self.y_min = QLineEdit(str(base["y_min"]), self)
        form.addRow("Y min", self.y_min)
        self.y_max = QLineEdit(str(base["y_max"]), self)
        form.addRow("Y max", self.y_max)
        self.width = QLineEdit(str(base["width"] * 100), self)
        form.addRow("Width (%)", self.width)
        self.height = QLineEdit(str(base["height"] * 100), self)
        form.addRow("Height (%)", self.height)

        self.location = QComboBox(self)
        self.location.addItems(self.LOCATIONS)
        self.location.setCurrentText(str(base["location"]))
        form.addRow("Location", self.location)

        self.rectangle = QCheckBox("Mark zoom region on main plot", self)
        self.rectangle.setChecked(bool(base["rectangle"]))
        form.addRow("", self.rectangle)

        self.edgecolor = QLineEdit(str(base["edgecolor"]), self)
        form.addRow("Edge color", self.edgecolor)
        self.linewidth = QLineEdit(str(base["linewidth"]), self)
        form.addRow("Line width", self.linewidth)

        self.x_scale = QComboBox(self)
        self.x_scale.addItems(self.SCALES)
        self.x_scale.setCurrentText(base["x_scale"] or "inherit")
        form.addRow("Inset X scale", self.x_scale)
        self.y_scale = QComboBox(self)
        self.y_scale.addItems(self.SCALES)
        self.y_scale.setCurrentText(base["y_scale"] or "inherit")
        form.addRow("Inset Y scale", self.y_scale)

        note = QLabel(
            "The inset shares the main Scatter data and color normalization. "
            "Scale overrides affect only the inset view.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply = QPushButton("Apply inset", self)
        apply.clicked.connect(self._apply)
        actions.addWidget(apply)
        layout.addLayout(actions)

        self._dependent = (
            self.x_min,
            self.x_max,
            self.y_min,
            self.y_max,
            self.width,
            self.height,
            self.location,
            self.rectangle,
            self.edgecolor,
            self.linewidth,
            self.x_scale,
            self.y_scale,
        )
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        enabled = self.enabled.currentText() == "On"
        for widget in self._dependent:
            widget.setEnabled(enabled)

    def build_inset(self) -> InsetSpec | None:
        if self.enabled.currentText() == "Off":
            return None
        inset = InsetSpec(
            x_min=_finite_float(self.x_min.text(), "X minimum"),
            x_max=_finite_float(self.x_max.text(), "X maximum"),
            y_min=_finite_float(self.y_min.text(), "Y minimum"),
            y_max=_finite_float(self.y_max.text(), "Y maximum"),
            width=_finite_float(self.width.text(), "Width") / 100.0,
            height=_finite_float(self.height.text(), "Height") / 100.0,
            location=self.location.currentText(),
            rectangle=self.rectangle.isChecked(),
            edgecolor=self.edgecolor.text(),
            linewidth=_finite_float(self.linewidth.text(), "Line width"),
            x_scale=None if self.x_scale.currentText() == "inherit" else self.x_scale.currentText(),
            y_scale=None if self.y_scale.currentText() == "inherit" else self.y_scale.currentText(),
        )
        validate_scatter(
            ScatterSpec(
                "x",
                "y",
                x_axis=AxisSpec(self._main_x_scale),
                y_axis=AxisSpec(self._main_y_scale),
                inset=inset,
            )
        )
        return inset

    def _apply(self) -> None:
        try:
            inset = self.build_inset()
        except Exception as error:
            QMessageBox.warning(self, "Invalid inset", str(error))
            return
        self.inset_applied.emit(inset)
        self.accept()
