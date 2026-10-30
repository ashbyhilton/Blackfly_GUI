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
from .analysis import LINE_PEN, AnalysisContext, AnalysisROI

FIT_PEN = pg.mkPen((255, 80, 255), width=2, style=Qt.PenStyle.DashLine)


class ProjectionTab(QWidget):
    ROWS = ["Centre", "σ", "FWHM", "1/e² diameter"]

    def __init__(self, ctx: AnalysisContext, aroi: AnalysisROI, controller: CameraController,
                 scale_um: Callable[[], float]) -> None:
        super().__init__()
        self.ctx, self.aroi, self.controller, self.scale_um = ctx, aroi, controller, scale_um
        self.fits: tuple[Fit1D, Fit1D] | None = None
        self._offsets = (0, 0)

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
        self.curve_x = self.plot_x.plot(pen=LINE_PEN)
        self.curve_y = self.plot_y.plot(pen=LINE_PEN)
        self.fit_x = self.plot_x.plot(pen=FIT_PEN)
        self.fit_y = self.plot_y.plot(pen=FIT_PEN)

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
        px, py = projections(region)
        xs, ys = np.arange(px.size) + x_off, np.arange(py.size) + y_off
        self.curve_x.setData(xs, px)
        self.curve_y.setData(ys, py)
        if not self.fit.isChecked():
            self.fits = None
            self.fit_x.setData([], [])
            self.fit_y.setData([], [])
            self._show_table()
            return
        self.fits = fit_gaussian_1d(px), fit_gaussian_1d(py)
        self._offsets = (x_off, y_off)
        for f, curve, axis in ((self.fits[0], self.fit_x, xs), (self.fits[1], self.fit_y, ys)):
            if f.success:
                curve.setData(axis, gaussian1d(axis - axis[0], f.amplitude, f.centre, f.sigma, f.offset))
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
