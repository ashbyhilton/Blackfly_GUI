"""X/Y projections tab: sums of the analysis ROI along columns and rows, with
1D Gaussian fits (centre, sigma, FWHM, 1/e^2 diameter)."""

from __future__ import annotations

from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analysis.gaussian import FWHM_PER_SIGMA
from ..analysis.profile1d import Fit1D, fit_gaussian_1d, gaussian1d, projections
from ..camera.controller import CameraController
from .analysis import AnalysisContext, AnalysisROI
from .worker import BackgroundTask

# 1 px pens: thick and dashed lines are several times slower to draw.
DATA_PEN = pg.mkPen((0, 200, 255), width=1)
FIT_PEN = pg.mkPen((255, 80, 255), width=1, style=Qt.PenStyle.DashLine)
FIT_POINTS = 200  # the fitted curve is smooth; fewer points draw faster


def _project(region: np.ndarray, fit: bool) -> tuple[np.ndarray, np.ndarray, tuple[Fit1D, Fit1D] | None]:
    """Runs on the worker thread."""
    px, py = projections(region)
    return px, py, (fit_gaussian_1d(px), fit_gaussian_1d(py)) if fit else None


class SteadyRange:
    """Keeps a plot's range still: rescales only when the data leaves the view
    or fills less than a third of it. pyqtgraph's continuous auto-range
    re-lays out the plot on every update, which cost the live view ~1/3 of
    its frame rate in tests -- far more than the analysis itself. Zooming
    with the mouse pauses rescaling until the plot's "A" button is pressed."""

    def __init__(self, plot: pg.PlotWidget) -> None:
        self.vb = plot.getViewBox()
        self.vb.disableAutoRange()
        self.manual = False
        self.vb.sigRangeChangedManually.connect(self._on_manual)

    def _on_manual(self, *_args) -> None:
        self.manual = True

    def update(self, x: np.ndarray, y: np.ndarray) -> None:
        if any(self.vb.state["autoRange"]):  # "A" button: back to steady auto-scaling
            self.vb.disableAutoRange()
            self.manual = False
        finite = y[np.isfinite(y)]
        if self.manual or x.size < 2 or finite.size == 0:
            return
        (x0, x1), (y0, y1) = self.vb.viewRange()
        if (x0, x1) != (x[0], x[-1]):
            self.vb.setXRange(float(x[0]), float(x[-1]), padding=0)
        lo, hi = float(finite.min()), float(finite.max())
        span = max(hi - lo, 1e-9)
        if lo < y0 or hi > y1 or y1 - y0 > 3 * span:
            self.vb.setYRange(lo - 0.05 * span, hi + 0.05 * span, padding=0)


class ProjectionTab(QWidget):
    ROWS = ["Centre", "σ", "FWHM", "1/e² diameter"]

    def __init__(self, ctx: AnalysisContext, aroi: AnalysisROI, controller: CameraController,
                 scale_um: Callable[[], float]) -> None:
        super().__init__()
        self.ctx, self.aroi, self.controller, self.scale_um = ctx, aroi, controller, scale_um
        self.fits: tuple[Fit1D, Fit1D] | None = None
        self._offsets = (0, 0)
        self.task = BackgroundTask(self._on_result, self)

        self.enable = QCheckBox("X/Y projections")
        self.enable.setToolTip("Sum the analysis ROI (yellow box), or the whole image, along columns (x) "
                               "and rows (y).")
        self.enable.toggled.connect(lambda _on: self.update_view())
        self.fit = QCheckBox("Fit Gaussians")
        self.fit.setChecked(True)
        self.fit.setToolTip("1D Gaussian + offset fit to each projection. Sums average the noise down, "
                            "so this works on spots too faint for the 2D fit.")
        self.fit.toggled.connect(lambda _on: self.update_view())
        copy = QPushButton("Copy results")
        copy.setToolTip("Copy the table to the clipboard (tab separated, pastes into Excel).")
        copy.clicked.connect(self._copy)

        self.plot_x = self._plot("x (px)")
        self.plot_y = self._plot("y (px)")
        self.curve_x = self.plot_x.plot(pen=DATA_PEN)
        self.curve_y = self.plot_y.plot(pen=DATA_PEN)
        self.fit_x = self.plot_x.plot(pen=FIT_PEN)
        self.fit_y = self.plot_y.plot(pen=FIT_PEN)
        self.range_x, self.range_y = SteadyRange(self.plot_x), SteadyRange(self.plot_y)

        self.table = QTableWidget(len(self.ROWS), 4)
        self.table.setHorizontalHeaderLabels(["x (px)", "x (µm)", "y (px)", "y (µm)"])
        self.table.setVerticalHeaderLabels(self.ROWS)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        top = QHBoxLayout()
        for w in (self.enable, self.fit, copy):
            top.addWidget(w)
        top.addStretch(1)
        plots = QHBoxLayout()
        plots.addWidget(self.plot_x, 1)
        plots.addWidget(self.plot_y, 1)
        plots.addWidget(self.table, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(plots, 1)

    @staticmethod
    def _plot(xlabel: str) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setLabel("bottom", xlabel)
        plot.setLabel("left", "Σ counts")
        plot.getAxis("bottom").enableAutoSIPrefix(False)
        plot.showGrid(x=True, y=True, alpha=0.3)
        return plot

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.update_view()  # it skips work while hidden

    # called at 10 Hz by the dock
    def update_view(self) -> None:
        img = self.ctx.display
        if img is None or not self.enable.isChecked() or not self.isVisible():
            return
        sl = self.aroi.slices(img.shape)
        region, x_off, y_off = (img, 0, 0) if sl is None else (img[sl], sl[1].start, sl[0].start)
        self.task.submit(lambda: _project(region, self.fit.isChecked()), (x_off, y_off))

    def _on_result(self, result: tuple | Exception, offsets: tuple[int, int]) -> None:
        if isinstance(result, Exception) or not self.enable.isChecked():
            return
        px, py, fits = result
        x_off, y_off = offsets
        xs, ys = np.arange(px.size) + x_off, np.arange(py.size) + y_off
        self.curve_x.setData(xs, px)
        self.curve_y.setData(ys, py)
        self.range_x.update(xs, px)
        self.range_y.update(ys, py)
        self.fits, self._offsets = fits, offsets
        if fits is None:
            self.fit_x.setData([], [])
            self.fit_y.setData([], [])
            self._show_table()
            return
        for f, curve, axis in ((fits[0], self.fit_x, xs), (fits[1], self.fit_y, ys)):
            if f.success:
                t = np.linspace(0.0, axis.size - 1.0, FIT_POINTS)
                curve.setData(t + axis[0], gaussian1d(t, f.amplitude, f.centre, f.sigma, f.offset))
            else:
                curve.setData([], [])
        self._show_table()

    def _show_table(self) -> None:
        s = self.scale_um()
        for axis, f in enumerate(self.fits or (None, None)):
            if f is None or not f.success:
                values: list[float | None] = [None] * len(self.ROWS)
            else:
                values = [f.centre + self._offsets[axis], f.sigma, FWHM_PER_SIGMA * f.sigma, 4 * f.sigma]
            for row, v in enumerate(values):
                self.table.setItem(row, 2 * axis, QTableWidgetItem("—" if v is None else f"{v:.2f}"))
                self.table.setItem(row, 2 * axis + 1, QTableWidgetItem("—" if v is None else f"{v * s:.2f}"))

    def _copy(self) -> None:
        lines = ["Parameter\tx (px)\tx (um)\ty (px)\ty (um)"]
        for row, name in enumerate(self.ROWS):
            cells = [self.table.item(row, c).text() if self.table.item(row, c) else "" for c in range(4)]
            lines.append("\t".join([name, *cells]))
        lines.append(f"Pixel size (um)\t{self.scale_um():.4f}")
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.controller.message.emit("Projection fit results copied to clipboard")
