"""F4.B Qt advanced Scatter parity and structural sampling evidence."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import shiboken6
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from csv_analyzer.data import CancelledError, DataError, Dataset, describe_columns
from csv_analyzer.plots import (
    AxisSpec,
    InsetSpec,
    Overlay,
    ScatterSpec,
    prepare_scatter,
    validate_overlay,
    validate_scatter,
)
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.scatter_inset_dialog import ScatterInsetDialog
from csv_analyzer.qt_app.scatter_overlay_dialog import ScatterOverlayDialog
from csv_analyzer.qt_app.scatter_style_dialog import ScatterStyle, ScatterStyleDialog
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f4b-tests"])


def pump_until(predicate, timeout_ms=10000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    expired = {"value": False}
    poll.setInterval(2)
    timeout.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)

    def expire():
        expired["value"] = True
        loop.quit()

    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if expired["value"] and not predicate():
        raise AssertionError("Qt condition did not become true before timeout")


def make_dataset(frame: pd.DataFrame, name="scatter.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


def base_frame(rows=8):
    x = np.arange(1, rows + 1, dtype=float)
    return pd.DataFrame(
        {
            "x": x,
            "y": x * 2.0,
            "z": x * 3.0,
            "c": x + 0.5,
        }
    )


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class ScatterAdvancedTests(unittest.TestCase):
    def make(self):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell)
        coordinator.start()
        self.addCleanup(self.close, coordinator, shell)
        return coordinator, shell

    def close(self, coordinator, shell):
        if coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            coordinator.begin_close()
        if coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        shell.deleteLater()
        coordinator.deleteLater()
        APP.processEvents()

    def commit(self, coordinator, frame, name="scatter.csv"):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, make_dataset(frame, name)))
        APP.processEvents()

    def plot(self, coordinator):
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()

    def test_style_defaults_save_cancel_and_spec_mapping(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.scatter_view
        self.assertEqual(view.style, ScatterStyle())
        self.assertEqual(view.overlays, ())
        self.assertIsNone(view.inset)

        dialog = view.open_style_dialog()
        original = view.style
        dialog.title.setText("cancelled")
        dialog.reject()
        APP.processEvents()
        self.assertEqual(view.style, original)

        dialog = view.open_style_dialog()
        dialog.title.setText("Custom title")
        dialog.x_label.setText("Custom X")
        dialog.y_label.setText("Custom Y")
        dialog.color_label.setText("Color value")
        dialog.marker.setCurrentText("^")
        dialog.size.setText("42")
        dialog.opacity.setText("0.55")
        dialog.excluded_opacity.setText("0.2")
        dialog.cmap.setCurrentText("plasma")
        dialog.grid.setChecked(False)
        dialog.sampling.setCurrentText("Limit each group")
        dialog.sample_limit.setText("3")
        dialog._apply()
        APP.processEvents()

        self.assertEqual(view.style.title, "Custom title")
        spec = view.build_spec()
        self.assertEqual(spec.title, "Custom title")
        self.assertEqual(spec.x_axis.label, "Custom X")
        self.assertEqual(spec.y_axis.label, "Custom Y")
        self.assertEqual(spec.color_label, "Color value")
        self.assertEqual(spec.marker, "^")
        self.assertEqual(spec.size, 42.0)
        self.assertEqual(spec.opacity, 0.55)
        self.assertEqual(spec.excluded_opacity, 0.2)
        self.assertEqual(spec.cmap, "plasma")
        self.assertFalse(spec.grid)
        self.assertEqual(spec.sample_limit, 3)
        self.assertIn("Appearance updated", shell.status_label.text())

    def test_style_invalid_numeric_values_delegate_to_scatter_validation(self):
        cases = (
            ("size", "abc"),
            ("size", "0"),
            ("size", "1001"),
            ("opacity", "-0.1"),
            ("opacity", "1.1"),
            ("excluded_opacity", "1.5"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                dialog = ScatterStyleDialog(ScatterStyle())
                self.addCleanup(dialog.deleteLater)
                getattr(dialog, field).setText(value)
                with self.assertRaises(DataError):
                    dialog.build_style()

        dialog = ScatterStyleDialog(ScatterStyle())
        self.addCleanup(dialog.deleteLater)
        dialog.sampling.setCurrentText("Limit each group")
        dialog.sample_limit.setText("0")
        with self.assertRaises(DataError):
            dialog.build_style()

    def test_overlay_supported_kinds_crud_order_validation_and_cancel(self):
        dialog = ScatterOverlayDialog(())
        self.addCleanup(dialog.deleteLater)
        for kind in ScatterOverlayDialog.KINDS:
            with self.subTest(kind=kind):
                dialog.kind.setCurrentText(kind)
                if kind == "Text":
                    dialog.fields["text"].setText("note")
                item = dialog.build_item()
                self.assertEqual(item.kind, kind)
                validate_overlay(item)

        dialog.new_item()
        dialog.kind.setCurrentText("Horizontal line")
        dialog.fields["y"].setText("1")
        dialog.add_or_update()
        dialog.kind.setCurrentText("Text")
        dialog.fields["x"].setText("2")
        dialog.fields["y"].setText("3")
        dialog.fields["text"].setText("second")
        dialog.add_or_update()
        self.assertEqual([item.kind for item in dialog.staged_items], ["Horizontal line", "Text"])

        dialog.list.setCurrentRow(0)
        dialog.fields["y"].setText("5")
        dialog.add_or_update()
        self.assertEqual(dialog.staged_items[0].y, 5.0)
        self.assertEqual(dialog.staged_items[1].text, "second")

        dialog.list.setCurrentRow(1)
        dialog.remove_selected()
        self.assertEqual(len(dialog.staged_items), 1)
        dialog.clear_all()
        self.assertEqual(dialog.staged_items, ())

        invalid = (
            Overlay("Point", color="not-a-color"),
            Overlay("Point", opacity=2),
            Overlay("Point", marker="not-a-marker"),
            Overlay("Horizontal line", linestyle="invalid"),
            Overlay("Point", size=0),
            Overlay("Text", text=""),
            Overlay("Point", x=float("inf")),
        )
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(DataError):
                    validate_overlay(item)

        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.scatter_view
        existing = (Overlay("Vertical line", x=2.0),)
        view.overlays = existing
        view.overlay_button.setText("Annotations · 1")
        opened = view.open_overlay_dialog()
        opened.kind.setCurrentText("Text")
        opened.fields["text"].setText("staged only")
        opened.add_or_update()
        opened.reject()
        APP.processEvents()
        self.assertEqual(view.overlays, existing)

    def test_inset_on_off_validation_scales_size_location_and_cancel(self):
        dialog = ScatterInsetDialog(None, x_scale="linear", y_scale="linear")
        self.addCleanup(dialog.deleteLater)
        self.assertIsNone(dialog.build_inset())

        dialog.enabled.setCurrentText("On")
        dialog.x_min.setText("1")
        dialog.x_max.setText("4")
        dialog.y_min.setText("2")
        dialog.y_max.setText("8")
        dialog.width.setText("30")
        dialog.height.setText("40")
        dialog.location.setCurrentText("lower left")
        dialog.edgecolor.setText("#123456")
        dialog.linewidth.setText("2")
        dialog.x_scale.setCurrentText("linear")
        dialog.y_scale.setCurrentText("log")
        inset = dialog.build_inset()
        self.assertEqual((inset.x_min, inset.x_max), (1.0, 4.0))
        self.assertEqual((inset.width, inset.height), (0.3, 0.4))
        self.assertEqual(inset.location, "lower left")
        self.assertEqual(inset.y_scale, "log")

        for field, value in (
            ("x_min", "5"),
            ("y_min", "9"),
            ("width", "5"),
            ("width", "90"),
            ("height", "5"),
            ("height", "90"),
            ("edgecolor", "bad-color"),
            ("linewidth", "0"),
        ):
            bad = ScatterInsetDialog(None, x_scale="linear", y_scale="linear")
            self.addCleanup(bad.deleteLater)
            bad.enabled.setCurrentText("On")
            bad.x_min.setText("1")
            bad.x_max.setText("4")
            bad.y_min.setText("2")
            bad.y_max.setText("8")
            bad.width.setText("35")
            bad.height.setText("35")
            setattr(bad, field, getattr(bad, field))
            getattr(bad, field).setText(value)
            with self.subTest(field=field, value=value):
                with self.assertRaises(DataError):
                    bad.build_inset()

        log_bad = ScatterInsetDialog(None, x_scale="log", y_scale="linear")
        self.addCleanup(log_bad.deleteLater)
        log_bad.enabled.setCurrentText("On")
        log_bad.x_min.setText("0")
        log_bad.x_max.setText("2")
        with self.assertRaises(DataError):
            log_bad.build_inset()

        with self.assertRaises(DataError):
            validate_scatter(
                ScatterSpec("x", "y", inset=InsetSpec(1, 2, 1, 2, location="middle"))
            )

        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.scatter_view
        existing = InsetSpec(1, 3, 2, 6)
        view.inset = existing
        view.inset_button.setText("Inset zoom · on")
        opened = view.open_inset_dialog()
        opened.enabled.setCurrentText("Off")
        opened.reject()
        APP.processEvents()
        self.assertEqual(view.inset, existing)

    def test_settings_staged_do_not_mutate_app_state_or_current_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        self.plot(coordinator)
        view = shell.scatter_view
        figure = view.panel.figure
        before = coordinator.state.snapshot()

        dialog = view.open_style_dialog()
        dialog.title.setText("B")
        dialog._apply()
        APP.processEvents()
        overlay = Overlay("Horizontal line", y=4.0)
        view._apply_overlays((overlay,), view.settings_epoch)
        inset = InsetSpec(1, 3, 2, 6)
        view._apply_inset(inset, view.settings_epoch)

        after = coordinator.state.snapshot()
        self.assertIs(view.panel.figure, figure)
        self.assertEqual(
            (after.revision, after.dataset_revision, after.selection_revision, after.filters),
            (before.revision, before.dataset_revision, before.selection_revision, before.filters),
        )
        self.assertIs(after.mask, before.mask)
        self.assertIs(after.dataset.frame, before.dataset.frame)

    def test_reset_restores_all_defaults_and_clears_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.scatter_view
        view.x_axis.column.setCurrentText("z")
        view.y_axis.column.setCurrentText("x")
        view.x_axis.scale.setCurrentIndex(1)
        view.x_axis.absolute.setChecked(True)
        view.color.setCurrentText("c")
        view.color_absolute.setChecked(True)
        view.color_scale.setCurrentIndex(1)
        view.color_scientific.setChecked(True)
        view.show_excluded.setChecked(False)
        view.style = ScatterStyle(title="changed", marker="s", sample_limit=2)
        view.overlays = (Overlay("Vertical line", x=2),)
        view.inset = InsetSpec(1, 3, 2, 6)
        self.plot(coordinator)
        self.assertTrue(view.panel.has_figure)

        coordinator.scatter_controller.reset_settings()
        self.assertFalse(view.panel.has_figure)
        self.assertEqual(view.x_axis.column.currentText(), "x")
        self.assertEqual(view.y_axis.column.currentText(), "y")
        self.assertEqual(view.x_axis.scale.currentData(), "linear")
        self.assertFalse(view.x_axis.absolute.isChecked())
        self.assertIsNone(view.color.currentData())
        self.assertFalse(view.color_absolute.isChecked())
        self.assertEqual(view.color_scale.currentData(), "linear")
        self.assertFalse(view.color_scientific.isChecked())
        self.assertTrue(view.show_excluded.isChecked())
        self.assertEqual(view.style, ScatterStyle())
        self.assertEqual(view.overlays, ())
        self.assertIsNone(view.inset)
        self.assertEqual(view.overlay_button.text(), "Annotations · 0")
        self.assertEqual(view.inset_button.text(), "Inset zoom · off")
        self.assertEqual(view.local_status.text(), "Settings reset. Plot again.")

    def test_dataset_resets_advanced_selection_preserves_it(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame(), "a.csv")
        view = shell.scatter_view
        style = ScatterStyle(title="persist", sample_limit=3)
        overlays = (Overlay("Horizontal line", y=2),)
        inset = InsetSpec(1, 3, 2, 6)
        view.style = style
        view.overlays = overlays
        view.inset = inset

        snapshot = coordinator.state.snapshot()
        coordinator.state.commit_filters(
            snapshot.revision,
            (FilterRule("x", lower=3),),
            np.array([False, False, True, True, True, True, True, True]),
        )
        APP.processEvents()
        self.assertEqual(view.style, style)
        self.assertEqual(view.overlays, overlays)
        self.assertEqual(view.inset, inset)

        self.commit(coordinator, base_frame(5).rename(columns={"x": "u", "y": "v"}), "b.csv")
        self.assertEqual(view.style, ScatterStyle())
        self.assertEqual(view.overlays, ())
        self.assertIsNone(view.inset)
        self.assertEqual(view.x_axis.column.currentText(), "u")
        self.assertEqual(view.y_axis.column.currentText(), "v")

    def test_stale_dialogs_rejected_by_dataset_reset_reset_and_close(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame(), "a.csv")
        view = shell.scatter_view

        style_dialog = view.open_style_dialog()
        overlay_dialog = view.open_overlay_dialog()
        inset_dialog = view.open_inset_dialog()
        old_epoch = view.settings_epoch
        self.commit(coordinator, base_frame(5), "b.csv")
        self.assertGreater(view.settings_epoch, old_epoch)
        self.assertIsNone(view._style_dialog)
        self.assertIsNone(view._overlay_dialog)
        self.assertIsNone(view._inset_dialog)
        style_dialog.style_applied.emit(ScatterStyle(title="stale"))
        overlay_dialog.overlays_applied.emit((Overlay("Vertical line", x=1),))
        inset_dialog.inset_applied.emit(InsetSpec(1, 2, 2, 4))
        APP.processEvents()
        self.assertEqual(view.style, ScatterStyle())
        self.assertEqual(view.overlays, ())
        self.assertIsNone(view.inset)

        dialog = view.open_style_dialog()
        coordinator.scatter_controller.reset_settings()
        self.assertIsNone(view._style_dialog)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

        dialog = view.open_overlay_dialog()
        coordinator.begin_close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertIsNone(view._overlay_dialog)
        if shiboken6.isValid(dialog):
            self.assertFalse(dialog.isVisible())

    def test_plot_spec_colorbar_overlays_inset_and_transforms(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [-1.0, -2.0, -3.0, -4.0],
                "y": [2.0, 4.0, 6.0, 8.0],
                "c": [1.0, 2.0, 3.0, 4.0],
            }
        )
        self.commit(coordinator, frame)
        view = shell.scatter_view
        view.x_axis.absolute.setChecked(True)
        view.x_axis.scale.setCurrentIndex(1)
        view.color.setCurrentText("c")
        view.color_scale.setCurrentIndex(0)
        view.color_absolute.setChecked(True)
        view.color_scientific.setChecked(True)
        view.style = ScatterStyle(
            title="Advanced",
            x_label="|custom x|",
            y_label="custom y",
            color_label="custom c",
            marker="D",
            cmap="coolwarm",
            grid=False,
        )
        view.overlays = (
            Overlay("Horizontal line", y=4, color="red"),
            Overlay("Text", x=2, y=5, text="note", color="blue"),
        )
        view.inset = InsetSpec(1, 3.5, 2, 7, location="lower right")
        self.plot(coordinator)

        spec = coordinator.scatter_controller.current_spec
        self.assertEqual(spec.title, "Advanced")
        self.assertEqual(spec.cmap, "coolwarm")
        self.assertEqual(spec.marker, "D")
        self.assertTrue(spec.x_axis.absolute)
        self.assertEqual(spec.x_axis.scale, "log")
        self.assertEqual(len(spec.overlays), 2)
        self.assertIsNotNone(spec.inset)

        figure = view.panel.figure
        self.assertGreaterEqual(len(figure.axes), 2)
        main = figure.axes[0]
        self.assertEqual(len(main.child_axes), 1)
        self.assertEqual(main.get_title(loc="left"), "Advanced")
        self.assertEqual(main.get_xlabel(), "|custom x|")
        self.assertEqual(main.get_ylabel(), "custom y")
        self.assertTrue(any(text.get_text() == "note" for text in main.texts))
        self.assertTrue(any(ax.get_ylabel() == "custom c" for ax in figure.axes[1:]))

    def test_export_tracks_current_styled_and_inset_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.scatter_view
        self.plot(coordinator)
        figure_a = view.panel.figure

        dialog = view.open_style_dialog()
        dialog.title.setText("B")
        dialog._apply()
        view.inset = InsetSpec(1, 3, 2, 6)
        self.assertIs(view.panel.figure, figure_a)

        exported = []
        with patch(
            "csv_analyzer.qt_app.plot_panel.export_figure",
            side_effect=lambda figure, path, **_kwargs: exported.append(figure) or Path(path),
        ):
            with patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                return_value=Path("before.png"),
            ):
                view.panel.export_current_figure()
            self.plot(coordinator)
            figure_b = view.panel.figure
            with patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                return_value=Path("after.svg"),
            ):
                view.panel.export_current_figure()

        self.assertIs(exported[0], figure_a)
        self.assertIs(exported[1], figure_b)
        self.assertIsNot(figure_a, figure_b)
        self.assertEqual(figure_b.axes[0].get_title(loc="left"), "B")
        self.assertEqual(len(figure_b.axes[0].child_axes), 1)


class ScatterAdvancedStructuralTests(unittest.TestCase):
    def setUp(self):
        rows = 200_000
        x = np.arange(1, rows + 1, dtype=float)
        self.frame = pd.DataFrame({"x": x, "y": x * 2.0, "c": x * 0.5})
        self.mask = np.zeros(rows, dtype=bool)
        self.mask[:150_000] = True
        self.dataset = make_dataset(self.frame, "S-200K-SCATTER.csv")
        self.shell = RecordingShell()
        self.coordinator = QtCoordinator(self.shell, max_workers=2)
        self.coordinator.start()
        ticket = self.coordinator.state.begin_load()
        self.assertTrue(self.coordinator.state.commit_dataset(ticket, self.dataset))
        snapshot = self.coordinator.state.snapshot()
        self.assertTrue(
            self.coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", upper=150_000),),
                self.mask,
            )
        )
        APP.processEvents()

    def tearDown(self):
        if self.coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            self.coordinator.begin_close()
        if self.coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: self.coordinator.lifecycle is Lifecycle.CLOSED)
        self.shell.deleteLater()
        self.coordinator.deleteLater()
        APP.processEvents()

    def test_s200k_sampling_worker_counts_determinism_and_statistics_independence(self):
        view = self.shell.scatter_view
        view.style = ScatterStyle(sample_limit=500)
        gui = threading.get_ident()
        worker_threads = []

        real_prepare = prepare_scatter

        def observed(frame, spec, mask=None, *, cancel=None):
            worker_threads.append(threading.get_ident())
            return real_prepare(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", observed):
            self.coordinator.scatter_controller.request_plot()
            pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        self.assertTrue(worker_threads)
        self.assertTrue(all(thread != gui for thread in worker_threads))
        counts = self.coordinator.scatter_controller.current_counts
        self.assertEqual(counts[:4], (200_000, 150_000, 150_000, 50_000))
        self.assertEqual(counts[4:], (500, 500))
        self.assertEqual(self.coordinator.state.snapshot().selected_count, 150_000)

        spec = view.build_spec()
        first = real_prepare(self.frame, spec, self.mask)
        second = real_prepare(self.frame, spec, self.mask)
        np.testing.assert_array_equal(first.x, second.x)
        np.testing.assert_array_equal(first.y, second.y)
        np.testing.assert_array_equal(first.excluded_x, second.excluded_x)

        self.coordinator.statistics_controller.request_compute()
        pump_until(lambda: self.coordinator.runner.activity.physical == 0)
        stats = self.coordinator.statistics_controller.current_result.set_index("Column")
        self.assertEqual(stats.loc["x", "Rows"], 150_000)

    def test_s200k_latest_request_cancels_predecessor(self):
        view = self.shell.scatter_view
        entered = threading.Event()
        release = threading.Event()
        prepared_limits = []

        def controlled(frame, spec, mask=None, *, cancel=None):
            prepared_limits.append(spec.sample_limit)
            if spec.sample_limit == 1000:
                entered.set()
                release.wait(timeout=8)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            return prepare_scatter(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", controlled):
            view.style = ScatterStyle(sample_limit=1000)
            self.coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=3))
            view.style = ScatterStyle(sample_limit=250)
            self.coordinator.scatter_controller.request_plot()
            release.set()
            pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        self.assertIn(1000, prepared_limits)
        self.assertIn(250, prepared_limits)
        self.assertEqual(self.coordinator.scatter_controller.current_spec.sample_limit, 250)
        self.assertEqual(self.coordinator.scatter_controller.current_counts[4:], (250, 250))


if __name__ == "__main__":
    unittest.main()
