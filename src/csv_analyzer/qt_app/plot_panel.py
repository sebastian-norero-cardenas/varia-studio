"""QtAgg plot host with atomic figure replacement and local lifecycle ownership."""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class FigureToolbar(NavigationToolbar2QT):
    """Matplotlib navigation without the backend's independent Save workflow."""

    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems if item[0] != "Save")


class PlotPanel(QWidget):
    """Own exactly one visible Figure/canvas/toolbar tuple."""

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
        """Draw the candidate fully before replacing the last valid figure."""
        canvas = self.canvas_class(figure)
        canvas.setParent(self.host)
        toolbar = self.toolbar_class(canvas, self.host)
        canvas.hide()
        toolbar.hide()
        try:
            canvas.draw()
        except Exception:
            self._dispose_tuple(figure, canvas, toolbar)
            raise

        old = (self.figure, self.canvas, self.toolbar)
        self.figure, self.canvas, self.toolbar = figure, canvas, toolbar
        self.empty_label.hide()
        self.host_layout.addWidget(toolbar)
        self.host_layout.addWidget(canvas, 1)
        toolbar.show()
        canvas.show()
        self.caption.setText(description)
        self.caption.setStyleSheet("")
        self._dispose_tuple(*old)

    def clear(self, message: str | None = None) -> None:
        old = (self.figure, self.canvas, self.toolbar)
        self.figure = self.canvas = self.toolbar = None
        self._dispose_tuple(*old)
        self.empty_label.setText(message or self.empty_text)
        self.empty_label.show()
        self.caption.setText("No figure yet")
        self.caption.setStyleSheet("")

    def error(self, message: str) -> None:
        self.caption.setText(f"Figure not updated: {message}")
        self.caption.setStyleSheet("color: #a33;")

    @staticmethod
    def _dispose_tuple(figure, canvas, toolbar) -> None:
        if figure is None and canvas is None and toolbar is None:
            return
        if canvas is not None and toolbar is not None:
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
            if getattr(canvas, "toolbar", None) is toolbar:
                canvas.toolbar = None
            if getattr(toolbar, "canvas", None) is canvas:
                toolbar.canvas = None

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
