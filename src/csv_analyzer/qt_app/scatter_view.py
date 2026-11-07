"""Qt Scatter controls with staged advanced appearance configuration."""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..data import DataError, Dataset
from ..plots import AxisSpec, InsetSpec, Overlay, ScatterSpec, validate_scatter
from .plot_panel import PlotPanel
from .scatter_inset_dialog import ScatterInsetDialog
from .scatter_overlay_dialog import ScatterOverlayDialog
from .scatter_style_dialog import ScatterStyle, ScatterStyleDialog


def _optional_float(text: str, label: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as error:
        raise DataError(f"{label}: enter a finite number.") from error
    if not math.isfinite(value):
        raise DataError(f"{label}: enter a finite number.")
    return value


class AxisControls(QGroupBox):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.column = QComboBox(self)
        form.addRow("Column", self.column)
        self.scale = QComboBox(self)
        self.scale.addItem("Linear", "linear")
        self.scale.addItem("Log", "log")
        form.addRow("Scale", self.scale)
        self.absolute = QCheckBox("Absolute", self)
        form.addRow("", self.absolute)
        self.scientific = QCheckBox("Scientific labels", self)
        form.addRow("", self.scientific)
        self.minimum = QLineEdit(self)
        self.minimum.setPlaceholderText("Automatic")
        form.addRow("Minimum", self.minimum)
        self.maximum = QLineEdit(self)
        self.maximum.setPlaceholderText("Automatic")
        form.addRow("Maximum", self.maximum)

    def set_columns(self, columns: tuple[str, ...], current: str | None) -> None:
        self.column.clear()
        self.column.addItems(columns)
        if current in columns:
            self.column.setCurrentText(current)

    def reset_options(self) -> None:
        self.scale.setCurrentIndex(0)
        self.absolute.setChecked(False)
        self.scientific.setChecked(False)
        self.minimum.clear()
        self.maximum.clear()

    def spec(self, label: str = "") -> AxisSpec:
        return AxisSpec(
            scale=self.scale.currentData(),
            scientific=self.scientific.isChecked(),
            absolute=self.absolute.isChecked(),
            lower=_optional_float(self.minimum.text(), f"{self.title()} minimum"),
            upper=_optional_float(self.maximum.text(), f"{self.title()} maximum"),
            label=label,
        )


class ScatterView(QWidget):
    plot_requested = Signal()
    reset_requested = Signal()
    settings_updated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dataset: Dataset | None = None
        self._settings_epoch = 0
        self._advanced_revision = 0
        self._style_dialog: ScatterStyleDialog | None = None
        self._overlay_dialog: ScatterOverlayDialog | None = None
        self._inset_dialog: ScatterInsetDialog | None = None
        self.style = ScatterStyle()
        self.overlays: tuple[Overlay, ...] = ()
        self.inset: InsetSpec | None = None

        outer = QHBoxLayout(self)
        controls_scroll = QScrollArea(self)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setMinimumWidth(320)
        controls_host = QWidget(controls_scroll)
        controls = QVBoxLayout(controls_host)

        self.x_axis = AxisControls("X axis", controls_host)
        self.y_axis = AxisControls("Y axis", controls_host)
        controls.addWidget(self.x_axis)
        controls.addWidget(self.y_axis)

        color_group = QGroupBox("Color", controls_host)
        color_form = QFormLayout(color_group)
        self.color = QComboBox(color_group)
        self.color.addItem("No color", None)
        self.color.currentIndexChanged.connect(self._sync_color_controls)
        color_form.addRow("Column", self.color)
        self.color_scale = QComboBox(color_group)
        self.color_scale.addItem("Linear", "linear")
        self.color_scale.addItem("Log", "log")
        color_form.addRow("Scale", self.color_scale)
        self.color_absolute = QCheckBox("Absolute", color_group)
        color_form.addRow("", self.color_absolute)
        self.color_scientific = QCheckBox("Scientific labels", color_group)
        color_form.addRow("", self.color_scientific)
        controls.addWidget(color_group)

        self.show_excluded = QCheckBox("Show filtered-out rows", controls_host)
        self.show_excluded.setChecked(True)
        controls.addWidget(self.show_excluded)

        advanced_group = QGroupBox("Refine the figure", controls_host)
        advanced_layout = QVBoxLayout(advanced_group)
        self.style_button = QPushButton("Appearance & sampling…", advanced_group)
        self.style_button.clicked.connect(self.open_style_dialog)
        advanced_layout.addWidget(self.style_button)
        self.overlay_button = QPushButton("Annotations · 0", advanced_group)
        self.overlay_button.clicked.connect(self.open_overlay_dialog)
        advanced_layout.addWidget(self.overlay_button)
        self.inset_button = QPushButton("Inset zoom · off", advanced_group)
        self.inset_button.clicked.connect(self.open_inset_dialog)
        advanced_layout.addWidget(self.inset_button)
        controls.addWidget(advanced_group)

        self.local_status = QLabel("Load a dataset to plot relationships.", controls_host)
        self.local_status.setWordWrap(True)
        controls.addWidget(self.local_status)

        self.plot_button = QPushButton("Plot relationships", controls_host)
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self.plot_requested)
        controls.addWidget(self.plot_button)

        self.reset_button = QPushButton("Reset settings", controls_host)
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self.reset_requested)
        controls.addWidget(self.reset_button)
        controls.addStretch(1)

        controls_scroll.setWidget(controls_host)
        outer.addWidget(controls_scroll)

        self.panel = PlotPanel(
            "Choose numeric X and Y columns, then plot.",
            self,
        )
        outer.addWidget(self.panel, 1)
        self._sync_color_controls()
        self._sync_advanced_buttons(False)

    @property
    def settings_epoch(self) -> int:
        return self._settings_epoch

    @property
    def advanced_revision(self) -> int:
        return self._advanced_revision

    def set_dataset(self, dataset: Dataset | None) -> None:
        self._dataset = dataset
        self._reset_controls(dataset)

    def reset_settings(self) -> None:
        self._reset_controls(self._dataset)

    def _reset_controls(self, dataset: Dataset | None) -> None:
        self.close_advanced_dialogs()
        self._settings_epoch += 1
        self._advanced_revision += 1
        columns = dataset.numeric_columns if dataset is not None else ()
        x_current = columns[0] if columns else None
        y_current = columns[1] if len(columns) > 1 else (columns[0] if columns else None)
        self.x_axis.set_columns(columns, x_current)
        self.y_axis.set_columns(columns, y_current)
        self.x_axis.reset_options()
        self.y_axis.reset_options()

        self.color.clear()
        self.color.addItem("No color", None)
        for column in columns:
            self.color.addItem(column, column)
        self.color.setCurrentIndex(0)
        self.color_scale.setCurrentIndex(0)
        self.color_absolute.setChecked(False)
        self.color_scientific.setChecked(False)
        self.show_excluded.setChecked(True)

        self.style = ScatterStyle()
        self.overlays = ()
        self.inset = None
        self.overlay_button.setText("Annotations · 0")
        self.inset_button.setText("Inset zoom · off")

        if dataset is None:
            message = "Load a dataset to plot relationships."
            enabled = False
        elif not len(dataset.frame):
            message = "The loaded dataset is empty."
            enabled = False
        elif len(columns) < 2:
            message = "Scatter requires at least two numeric columns."
            enabled = False
        else:
            message = f"{len(columns)} numeric columns available."
            enabled = True
        self.local_status.setText(message)
        self.plot_button.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self._sync_advanced_buttons(enabled)
        self._sync_color_controls()

    def _sync_advanced_buttons(self, enabled: bool) -> None:
        self.style_button.setEnabled(enabled)
        self.overlay_button.setEnabled(enabled)
        self.inset_button.setEnabled(enabled)

    def build_spec(self) -> ScatterSpec:
        if self._dataset is None:
            raise DataError("Load a dataset before plotting.")
        if not self.plot_button.isEnabled():
            raise DataError(self.local_status.text())
        style = self.style
        spec = ScatterSpec(
            self.x_axis.column.currentText(),
            self.y_axis.column.currentText(),
            x_axis=self.x_axis.spec(style.x_label),
            y_axis=self.y_axis.spec(style.y_label),
            color=self.color.currentData(),
            color_absolute=self.color_absolute.isChecked(),
            color_scale=self.color_scale.currentData(),
            color_scientific=self.color_scientific.isChecked(),
            color_label=style.color_label,
            cmap=style.cmap,
            marker=style.marker,
            size=style.size,
            opacity=style.opacity,
            show_excluded=self.show_excluded.isChecked(),
            excluded_opacity=style.excluded_opacity,
            title=style.title,
            grid=style.grid,
            sample_limit=style.sample_limit,
            overlays=self.overlays,
            inset=self.inset,
        )
        validate_scatter(spec)
        return spec

    def set_feedback(self, text: str) -> None:
        self.local_status.setText(text)

    def open_style_dialog(self) -> ScatterStyleDialog | None:
        if not self.style_button.isEnabled():
            return None
        if self._dialog_valid(self._style_dialog):
            self._style_dialog.raise_()
            self._style_dialog.activateWindow()
            return self._style_dialog

        epoch = self._settings_epoch
        dialog = ScatterStyleDialog(self.style, self)
        dialog.style_applied.connect(lambda value: self._apply_style(value, epoch))
        dialog.finished.connect(lambda _result: self._clear_style_dialog(dialog))
        self._style_dialog = dialog
        dialog.open()
        return dialog

    def _apply_style(self, style: object, epoch: int) -> None:
        if epoch != self._settings_epoch or not isinstance(style, ScatterStyle):
            return
        if style == self.style:
            return
        self.style = style
        self._advanced_revision += 1
        message = "Appearance updated. Plot again to apply it."
        self.set_feedback(message)
        self.settings_updated.emit(message)

    def _clear_style_dialog(self, dialog: ScatterStyleDialog) -> None:
        if self._style_dialog is dialog:
            self._style_dialog = None

    def open_overlay_dialog(self) -> ScatterOverlayDialog | None:
        if not self.overlay_button.isEnabled():
            return None
        if self._dialog_valid(self._overlay_dialog):
            self._overlay_dialog.raise_()
            self._overlay_dialog.activateWindow()
            return self._overlay_dialog

        epoch = self._settings_epoch
        dialog = ScatterOverlayDialog(self.overlays, self)
        dialog.overlays_applied.connect(lambda value: self._apply_overlays(value, epoch))
        dialog.finished.connect(lambda _result: self._clear_overlay_dialog(dialog))
        self._overlay_dialog = dialog
        dialog.open()
        return dialog

    def _apply_overlays(self, overlays: object, epoch: int) -> None:
        if epoch != self._settings_epoch or not isinstance(overlays, tuple):
            return
        if overlays == self.overlays:
            return
        self.overlays = overlays
        self._advanced_revision += 1
        self.overlay_button.setText(f"Annotations · {len(overlays)}")
        message = "Annotations updated. Plot again to apply them."
        self.set_feedback(message)
        self.settings_updated.emit(message)

    def _clear_overlay_dialog(self, dialog: ScatterOverlayDialog) -> None:
        if self._overlay_dialog is dialog:
            self._overlay_dialog = None

    def open_inset_dialog(self) -> ScatterInsetDialog | None:
        if not self.inset_button.isEnabled():
            return None
        if self._dialog_valid(self._inset_dialog):
            self._inset_dialog.raise_()
            self._inset_dialog.activateWindow()
            return self._inset_dialog

        epoch = self._settings_epoch
        dialog = ScatterInsetDialog(
            self.inset,
            x_scale=str(self.x_axis.scale.currentData()),
            y_scale=str(self.y_axis.scale.currentData()),
            parent=self,
        )
        dialog.inset_applied.connect(lambda value: self._apply_inset(value, epoch))
        dialog.finished.connect(lambda _result: self._clear_inset_dialog(dialog))
        self._inset_dialog = dialog
        dialog.open()
        return dialog

    def _apply_inset(self, inset: object, epoch: int) -> None:
        if epoch != self._settings_epoch:
            return
        if inset is not None and not isinstance(inset, InsetSpec):
            return
        if inset == self.inset:
            return
        self.inset = inset
        self._advanced_revision += 1
        self.inset_button.setText(f"Inset zoom · {'on' if inset is not None else 'off'}")
        message = "Inset updated. Plot again to apply it."
        self.set_feedback(message)
        self.settings_updated.emit(message)

    def _clear_inset_dialog(self, dialog: ScatterInsetDialog) -> None:
        if self._inset_dialog is dialog:
            self._inset_dialog = None

    @staticmethod
    def _dialog_valid(dialog: QDialog | None) -> bool:
        if dialog is None:
            return False
        try:
            import shiboken6

            return shiboken6.isValid(dialog)
        except RuntimeError:
            return False

    def close_advanced_dialogs(self) -> None:
        for dialog in (self._style_dialog, self._overlay_dialog, self._inset_dialog):
            if self._dialog_valid(dialog):
                dialog.reject()
        self._style_dialog = None
        self._overlay_dialog = None
        self._inset_dialog = None

    def _sync_color_controls(self) -> None:
        enabled = self.color.currentData() is not None
        self.color_scale.setEnabled(enabled)
        self.color_absolute.setEnabled(enabled)
        self.color_scientific.setEnabled(enabled)
