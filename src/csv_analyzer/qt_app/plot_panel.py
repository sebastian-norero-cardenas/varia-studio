"""QtAgg plot host with atomic figure replacement and local lifecycle ownership."""

from __future__ import annotations

import shiboken6
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..plots import export_figure
from .exporting import choose_save_path, close_export_dialogs, export_with_confirmation


class FigureToolbar(NavigationToolbar2QT):
    """Matplotlib navigation without the backend's independent Save workflow."""

    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems if item[0] != "Save")


class PlotPanel(QWidget):
    """Own exactly one visible Figure/canvas/toolbar tuple."""

    export_succeeded = Signal(str)
    export_failed = Signal(object)

    canvas_class = FigureCanvasQTAgg
    toolbar_class = FigureToolbar

    def __init__(self, empty_text: str, parent=None) -> None:
        super().__init__(parent)
        self.empty_text = empty_text
        self.figure: Figure | None = None
        self.canvas: FigureCanvasQTAgg | None = None
        self.toolbar: NavigationToolbar2QT | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.dpi = QComboBox(self)
        for value in (150, 300, 600):
            self.dpi.addItem(f"{value} DPI", value)
        self.dpi.setCurrentIndex(1)
        export_row.addWidget(self.dpi)
        self.export_button = QPushButton("Export figure…", self)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_current_figure)
        export_row.addWidget(self.export_button)
        layout.addLayout(export_row)

        self.host = QWidget(self)
        self.host_layout = QVBoxLayout(self.host)
        self.host_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_label = QLabel(empty_text, self.host)
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(self.empty_label.alignment().AlignCenter)
        self.host_layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.host, 1)

        self.caption = QLabel("No figure yet", self)
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

    @property
    def has_figure(self) -> bool:
        return self.figure is not None

    def show_figure(self, figure: Figure, description: str) -> None:
        """Prepare a candidate transactionally before replacing the active figure."""
        # Keep the private transaction root parent-owned so its C++ lifetime
        # remains stable until DeferredDelete; it is never inserted in host/layout.
        candidate_owner = QWidget(self)
        initial_canvas = getattr(figure, "canvas", None)
        canvas = None
        toolbar = None
        try:
            canvas = self.canvas_class(figure)
            canvas.setParent(candidate_owner)
            toolbar = self.toolbar_class(canvas, candidate_owner)
            canvas.hide()
            toolbar.hide()
            canvas.draw()

            # Ownership is transferred only after the candidate has drawn
            # successfully. Public state still points to the active tuple A.
            toolbar.setParent(self.host)
            canvas.setParent(self.host)
        except Exception:
            self._dispose_candidate(
                figure,
                initial_canvas,
                canvas,
                toolbar,
                candidate_owner,
            )
            raise

        if shiboken6.isValid(candidate_owner):
            shiboken6.delete(candidate_owner)
        old = (self.figure, self.canvas, self.toolbar)
        self.figure, self.canvas, self.toolbar = figure, canvas, toolbar
        self.empty_label.hide()
        self.host_layout.addWidget(toolbar)
        self.host_layout.addWidget(canvas, 1)
        toolbar.show()
        canvas.show()
        self.caption.setText(description)
        self.caption.setStyleSheet("")
        self.export_button.setEnabled(True)
        close_export_dialogs(self)
        self._dispose_tuple(*old)

    def clear(self, message: str | None = None) -> None:
        old = (self.figure, self.canvas, self.toolbar)
        self.figure = self.canvas = self.toolbar = None
        close_export_dialogs(self)
        self._dispose_tuple(*old)
        self.export_button.setEnabled(False)
        self.empty_label.setText(message or self.empty_text)
        self.empty_label.show()
        self.caption.setText("No figure yet")
        self.caption.setStyleSheet("")

    def error(self, message: str) -> None:
        self.caption.setText(f"Figure not updated: {message}")
        self.caption.setStyleSheet("color: #a33;")

    def export_current_figure(self) -> None:
        """Export the currently published Figure on the Qt GUI thread."""
        figure = self.figure
        if figure is None:
            return
        path = choose_save_path(
            self,
            title="Export displayed figure",
            initial_name="figure.png",
            name_filters=("PNG image (*.png)", "PDF (*.pdf)", "SVG (*.svg)"),
            default_suffix="png",
        )
        if path is None or self.figure is not figure:
            return

        dpi = int(self.dpi.currentData())

        def writer(overwrite: bool):
            if self.figure is not figure:
                return None
            return export_figure(figure, path, dpi=dpi, overwrite=overwrite)

        try:
            published = export_with_confirmation(self, path, writer)
        except Exception as error:
            self.export_failed.emit(error)
            return
        if published is not None:
            self.export_succeeded.emit(f"Figure exported to {published}")

    @classmethod
    def _dispose_candidate(
        cls,
        figure,
        initial_canvas,
        canvas,
        toolbar,
        candidate_owner,
    ) -> None:
        """Rollback an unpublished QtAgg candidate, including partial constructors."""
        candidate_canvas = canvas
        if candidate_canvas is None:
            attached = getattr(figure, "canvas", None)
            if attached is not initial_canvas:
                candidate_canvas = attached

        # A toolbar that returned normally is safe to detach explicitly. If its
        # constructor raised before returning, do not touch the partial wrapper:
        # candidate_owner is its Qt owner and will destroy it synchronously.
        cls._break_toolbar_links(candidate_canvas, toolbar)

        try:
            figure.clear()
        finally:
            try:
                figure.set_canvas(None)
            except Exception:
                pass

        for attached_canvas in (candidate_canvas, initial_canvas):
            if attached_canvas is None:
                continue
            try:
                if getattr(attached_canvas, "figure", None) is figure:
                    attached_canvas.figure = None
            except Exception:
                pass

        orphans = []
        for widget in (toolbar, candidate_canvas):
            if widget is None or not shiboken6.isValid(widget):
                continue
            try:
                if widget.parent() is not candidate_owner:
                    widget.setParent(candidate_owner)
            except RuntimeError:
                orphans.append(widget)

        # show_figure always runs on the GUI thread. Immediate deletion avoids
        # leaving partially constructed PySide wrappers pending in DeferredDelete
        # while the next figure is being built.
        if shiboken6.isValid(candidate_owner):
            shiboken6.delete(candidate_owner)
        for widget in orphans:
            if shiboken6.isValid(widget):
                shiboken6.delete(widget)

    @staticmethod
    def _neutralize_pending_draw(canvas) -> None:
        """Cancel a queued QtAgg draw_idle before detaching its Figure.

        Matplotlib 3.8-3.10 schedules _draw_idle through a zero-delay Qt
        timer and gates the callback with the private _draw_pending flag.
        Access stays defensive so a backend/version without that detail is a
        no-op rather than a lifecycle failure.
        """
        if canvas is None or not getattr(canvas, "_draw_pending", False):
            return
        try:
            setattr(canvas, "_draw_pending", False)
        except (AttributeError, RuntimeError):
            pass

    @staticmethod
    def _break_toolbar_links(canvas, toolbar) -> None:
        if canvas is None or toolbar is None:
            return
        for attribute in ("_id_press", "_id_release", "_id_drag"):
            callback_id = getattr(toolbar, attribute, None)
            if callback_id is not None:
                try:
                    canvas.mpl_disconnect(callback_id)
                except Exception:
                    pass
                try:
                    setattr(toolbar, attribute, None)
                except Exception:
                    pass
        try:
            if getattr(canvas, "toolbar", None) is toolbar:
                canvas.toolbar = None
        except Exception:
            pass
        try:
            if getattr(toolbar, "canvas", None) is canvas:
                toolbar.canvas = None
        except Exception:
            pass

    @staticmethod
    def _dispose_tuple(figure, canvas, toolbar) -> None:
        if figure is None and canvas is None and toolbar is None:
            return
        # A queued Qt callback may outlive this published tuple. Neutralize it
        # while canvas.figure is still valid, before any Figure detachment.
        PlotPanel._neutralize_pending_draw(canvas)
        PlotPanel._break_toolbar_links(canvas, toolbar)

        if toolbar is not None:
            toolbar.hide()
            toolbar.setParent(None)
            toolbar.deleteLater()
        if canvas is not None:
            canvas.hide()
            canvas.setParent(None)
        if figure is not None:
            try:
                figure.clear()
            finally:
                if canvas is not None and getattr(figure, "canvas", None) is canvas:
                    figure.set_canvas(None)
        if canvas is not None:
            try:
                canvas.figure = None
            except Exception:
                pass
            canvas.deleteLater()
