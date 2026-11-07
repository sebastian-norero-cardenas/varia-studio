"""F3.D scatter state, worker preparation and GUI-thread publication."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Slot

from ..data import CancelledError
from ..plots import ScatterData, ScatterSpec, prepare_scatter, render_scatter
from ..state import StateSnapshot
from .scatter_view import ScatterView


class ScatterController(QObject):
    def __init__(
        self,
        view: ScatterView,
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
        self._plot_epoch = 0
        self._dataset_revision = -1
        self._selection_revision = -1
        self.current_spec: ScatterSpec | None = None
        self.current_counts: tuple[int, int, int, int, int, int] | None = None
        view.plot_requested.connect(self.request_plot)
        view.reset_requested.connect(self.reset_settings)
        view.settings_updated.connect(self._settings_updated)

    def handle_state_change(self, reason: str, snapshot: StateSnapshot) -> None:
        dataset_changed = snapshot.dataset_revision != self._dataset_revision
        selection_changed = snapshot.selection_revision != self._selection_revision
        self._dataset_revision = snapshot.dataset_revision
        self._selection_revision = snapshot.selection_revision

        if dataset_changed:
            self._invalidate_pending()
            self.current_spec = None
            self.current_counts = None
            self.view.set_dataset(snapshot.dataset)
            self.view.panel.clear()
            return

        if selection_changed:
            self._invalidate_pending()
            self.current_counts = None
            self.view.panel.clear("Selection changed. Plot again to use the current filters.")
            self.view.set_feedback("Selection changed. Plot again to use the current filters.")

    @Slot()
    def reset_settings(self) -> None:
        if self._closing:
            return
        self._invalidate_pending()
        self.current_spec = None
        self.current_counts = None
        self.view.reset_settings()
        self.view.panel.clear("Settings reset. Plot again.")
        self.view.set_feedback("Settings reset. Plot again.")
        self._set_status("Settings reset. Plot again.")

    @Slot(str)
    def _settings_updated(self, message: str) -> None:
        if self._closing:
            return
        self._set_status(message)

    @Slot()
    def request_plot(self) -> None:
        if self._closing:
            return
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None:
            return
        try:
            spec = self.view.build_spec()
        except Exception as error:
            self.view.panel.error(str(error))
            self._present_error(error)
            return

        self._plot_epoch += 1
        epoch = self._plot_epoch
        plot_settings_revision = self.view.plot_settings_revision
        self.current_spec = spec
        frame = snapshot.dataset.frame
        mask = snapshot.mask
        self._set_status("Preparing relationship plot…")

        token = self._submit_task(
            "scatter",
            lambda cancel: prepare_scatter(frame, spec, mask, cancel=cancel),
            success=lambda data: self._install_scatter(
                data,
                spec,
                epoch,
                plot_settings_revision,
            ),
            failure=self._prepare_failure,
            receiver=self,
            exclusive_group="plots",
        )
        if token is None:
            self.view.panel.error("Scatter preparation could not be scheduled.")

    def _install_scatter(
        self,
        data: ScatterData,
        spec: ScatterSpec,
        epoch: int,
        plot_settings_revision: int,
    ) -> None:
        if self._closing or epoch != self._plot_epoch or spec != self.current_spec:
            return
        if threading.get_ident() != self._gui_thread_id:
            raise RuntimeError("Scatter rendering left the Qt GUI thread.")

        try:
            figure = render_scatter(data, spec)
            self.view.panel.show_figure(figure, data.description)
        except Exception as error:
            self.view.panel.error(str(error))
            self._present_error(error)
            return

        self.current_counts = (
            data.total,
            data.selected,
            data.valid,
            data.excluded_valid,
            len(data.x),
            len(data.excluded_x),
        )
        if plot_settings_revision != self.view.plot_settings_revision:
            warning = "Figure uses previous settings. Plot again to apply current settings."
            self.view.set_feedback(f"{data.description}\n{warning}")
            self._set_status(
                "Figure updated with previous settings. Plot again to apply current settings."
            )
        else:
            self.view.set_feedback(data.description)
            self._set_status("Relationship figure updated.")

    def _prepare_failure(self, error: BaseException) -> None:
        if isinstance(error, CancelledError):
            return
        self.view.panel.error(str(error))
        self._present_error(error)

    def _invalidate_pending(self) -> None:
        self._plot_epoch += 1
        self._cancel_task("scatter")

    def begin_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._invalidate_pending()
        self.view.close_advanced_dialogs()
        try:
            self.view.plot_requested.disconnect(self.request_plot)
            self.view.reset_requested.disconnect(self.reset_settings)
            self.view.settings_updated.disconnect(self._settings_updated)
        except (RuntimeError, TypeError):
            pass
        self.view.panel.clear()
