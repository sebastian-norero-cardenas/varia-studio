"""F3.F statistics state, worker execution and GUI-thread publication."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pandas as pd
from PySide6.QtCore import QObject, Slot

from ..analysis import categorical_summary, correlation_summary, numeric_summary
from ..data import CancelledError
from ..exports import export_statistics
from ..state import StateSnapshot
from .exporting import choose_save_path, export_with_confirmation
from .statistics_columns_dialog import StatisticsColumnsDialog
from .statistics_view import StatisticsRequest, StatisticsView


class StatisticsController(QObject):
    def __init__(
        self,
        view: StatisticsView,
        *,
        snapshot_provider: Callable[[], StateSnapshot],
        submit_task: Callable,
        cancel_task: Callable[[str], None],
        set_status: Callable[[str], None],
        present_error: Callable[[BaseException], None],
        gui_thread_id: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        self._snapshot_provider = snapshot_provider
        self._submit_task = submit_task
        self._cancel_task = cancel_task
        self._set_status = set_status
        self._present_error = present_error
        self._gui_thread_id = gui_thread_id
        self._closing = False
        self._statistics_epoch = 0
        self._dataset_revision = -1
        self._selection_revision = -1
        self._columns_dialog: StatisticsColumnsDialog | None = None
        self.current_mode: str | None = None
        self.current_settings: StatisticsRequest | None = None
        self.current_result: pd.DataFrame | None = None

        view.compute_requested.connect(self.request_compute)
        view.settings_changed.connect(self.settings_changed)
        view.choose_columns_requested.connect(self.open_columns_dialog)
        view.export_requested.connect(self.request_export)

    def handle_state_change(self, reason: str, snapshot: StateSnapshot) -> None:
        dataset_changed = snapshot.dataset_revision != self._dataset_revision
        selection_changed = snapshot.selection_revision != self._selection_revision
        self._dataset_revision = snapshot.dataset_revision
        self._selection_revision = snapshot.selection_revision

        if dataset_changed:
            self._invalidate_pending()
            self.current_mode = None
            self.current_settings = None
            self.current_result = None
            self._close_columns_dialog()
            self.view.set_dataset(snapshot.dataset)
            self.view.clear_result(
                "Choose a mode and compute statistics."
                if snapshot.dataset is not None
                else "Open a dataset to compute statistics."
            )
            return

        if selection_changed:
            self._invalidate_pending()
            self.current_result = None
            self.view.clear_result("Selection changed. Compute again to use the current filters.")

    @Slot()
    def settings_changed(self) -> None:
        if self._closing:
            return
        self._invalidate_pending()
        self.current_result = None
        self.view.clear_result("Settings changed. Compute again.")

    @Slot()
    def request_compute(self) -> None:
        if self._closing:
            return
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None:
            return
        try:
            request = self.view.build_request()
        except Exception as error:
            self.view.set_feedback(str(error))
            self._present_error(error)
            return

        self._statistics_epoch += 1
        epoch = self._statistics_epoch
        self.current_mode = request.mode
        self.current_settings = request
        self.current_result = None
        frame = snapshot.dataset.frame
        mask = snapshot.mask
        selected_count = snapshot.selected_count
        self.view.clear_result("Computing statistics…")
        self._set_status("Computing statistics…")

        def work(cancel):
            if request.mode == "numeric":
                return numeric_summary(frame, mask, ddof=request.ddof, cancel=cancel)
            if request.mode == "categorical":
                return categorical_summary(frame, mask, cancel=cancel)
            return correlation_summary(
                frame,
                request.correlation_columns,
                mask,
                cancel=cancel,
            )

        token = self._submit_task(
            "statistics",
            work,
            success=lambda result: self._install_result(
                result,
                request,
                epoch,
                selected_count,
            ),
            failure=self._compute_failure,
            receiver=self,
        )
        if token is None:
            self.view.set_feedback("Statistics computation could not be scheduled.")

    @Slot()
    def request_export(self) -> None:
        if self._closing:
            return
        result = self.current_result
        if result is None or result.empty or not self.view.export_button.isEnabled():
            return
        path = choose_save_path(
            self.view,
            title="Export statistics",
            initial_name="statistics.csv",
            name_filters=("CSV (*.csv)",),
            default_suffix="csv",
        )
        if (
            path is None
            or self._closing
            or self.current_result is not result
            or not self.view.export_button.isEnabled()
        ):
            return

        def writer(overwrite: bool):
            if self._closing or self.current_result is not result:
                return None
            return export_statistics(result, path, overwrite=overwrite)

        try:
            published = export_with_confirmation(self.view, path, writer)
        except Exception as error:
            self._present_error(error)
            return
        if published is not None:
            self._set_status(f"Statistics exported to {published}")

    @Slot()
    def open_columns_dialog(self) -> None:
        if self._closing:
            return
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None or len(snapshot.dataset.numeric_columns) < 2:
            return
        self._close_columns_dialog()
        dialog = StatisticsColumnsDialog(
            snapshot.dataset.numeric_columns,
            self.view.correlation_columns,
            parent=self.view,
        )
        dialog.columns_applied.connect(self._apply_correlation_columns)
        dialog.finished.connect(self._columns_dialog_finished)
        self._columns_dialog = dialog
        dialog.open()

    @Slot(object)
    def _apply_correlation_columns(self, columns: object) -> None:
        self.view.set_correlation_columns(tuple(columns))

    @Slot(int)
    def _columns_dialog_finished(self, _result: int) -> None:
        self._columns_dialog = None

    def _install_result(
        self,
        result: pd.DataFrame,
        request: StatisticsRequest,
        epoch: int,
        selected_count: int,
    ) -> None:
        if self._closing or epoch != self._statistics_epoch or request != self.current_settings:
            return
        if threading.get_ident() != self._gui_thread_id:
            raise RuntimeError("Statistics publication left the Qt GUI thread.")
        try:
            self.view.show_result(result, request, selected_count)
        except Exception as error:
            self.current_result = None
            self.view.clear_result("Could not display statistics result.")
            self._present_error(error)
            return
        self.current_result = result
        self._set_status("Statistics updated.")

    def _compute_failure(self, error: BaseException) -> None:
        if isinstance(error, CancelledError):
            return
        self.current_result = None
        self.view.clear_result(f"Could not compute statistics: {error}")
        self._present_error(error)

    def _invalidate_pending(self) -> None:
        self._statistics_epoch += 1
        self._cancel_task("statistics")

    def _close_columns_dialog(self) -> None:
        dialog = self._columns_dialog
        self._columns_dialog = None
        if dialog is not None:
            try:
                dialog.reject()
            except RuntimeError:
                pass

    def begin_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._invalidate_pending()
        self._close_columns_dialog()
        self.current_result = None
        self.view.close_export_dialogs()
        try:
            self.view.compute_requested.disconnect(self.request_compute)
            self.view.settings_changed.disconnect(self.settings_changed)
            self.view.choose_columns_requested.disconnect(self.open_columns_dialog)
            self.view.export_requested.disconnect(self.request_export)
        except (RuntimeError, TypeError):
            pass
        self.view.result_model.replace_result(None)
