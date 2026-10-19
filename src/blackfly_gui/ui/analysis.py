"""Analysis dock: ROI statistics, line profile (and more tabs added by the
main window). Work is done on a 10 Hz timer on the newest frame only, so
analysis never slows down the live view. Moving an overlay also triggers an
update, so analysis works on a still image while acquisition is stopped."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from scipy.ndimage import map_coordinates
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..analysis.stats import region_stats
from ..camera.controller import CameraController
from .image_view import ImageView

ROI_PEN = pg.mkPen((255, 210, 0), width=2)
LINE_PEN = pg.mkPen((0, 200, 255), width=2)


class AnalysisContext:
    """Latest data shared by all analysis tabs."""

    def __init__(self) -> None:
        self.display: np.ndarray | None = None  # what is shown (maybe reference-corrected)
        self.raw: np.ndarray | None = None  # camera data
        self.max_value: float = 255.0
        self.frame_id = -1


class AnalysisROI:
    """The yellow rectangle on the image, shared by statistics and Gaussian fit."""

    def __init__(self, view: ImageView) -> None:
        self.view = view
        self.roi = pg.RectROI((0, 0), (100, 100), pen=ROI_PEN, movable=True, rotatable=False,
                              removable=False)
        self.roi.addScaleHandle((0, 0), (1, 1))
        self.roi.addScaleHandle((1, 0), (0, 1))
        self.roi.addScaleHandle((0, 1), (1, 0))
        self.roi.setZValue(10)
        self.roi.setVisible(False)
        view.view.addItem(self.roi)
        self._shape: tuple[int, int] | None = None

    @property
    def visible(self) -> bool:
        return self.roi.isVisible()

    def set_visible(self, on: bool) -> None:
        self.roi.setVisible(on)

    def fit_to(self, shape: tuple[int, int]) -> None:
        """Place in the central quarter when the image shape changes or the
        ROI falls outside the image."""
        h, w = shape[:2]
        x, y, rw, rh = self.rect()
        if self._shape != (h, w) and (x + rw > w or y + rh > h or self._shape is None):
            self.roi.setPos((w // 4, h // 4), finish=False)
            self.roi.setSize((w // 2, h // 2), finish=False)
        self._shape = (h, w)

    def rect(self) -> tuple[int, int, int, int]:
        x, y = self.roi.pos()
        w, h = self.roi.size()
        return int(round(x)), int(round(y)), int(round(w)), int(round(h))

    def slices(self, shape: tuple[int, ...]) -> tuple[slice, slice] | None:
        """Clamped (rows, cols) slices, or None if the ROI is hidden/empty."""
        if not self.visible:
            return None
        x, y, w, h = self.rect()
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(shape[1], x + w), min(shape[0], y + h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return slice(y0, y1), slice(x0, x1)


class StatsTab(QWidget):
    def __init__(self, ctx: AnalysisContext, aroi: AnalysisROI, view: ImageView,
                 controller: CameraController) -> None:
        super().__init__()
        self.ctx, self.aroi, self.view, self.controller = ctx, aroi, view, controller

        self.show_roi = QCheckBox("Analysis ROI (yellow box)")
        self.show_roi.setToolTip("Drag/resize the box on the image. Statistics and the Gaussian "
                                 "fit use this region; without it they use the whole image.")
        self.show_roi.toggled.connect(self._toggle_roi)
        self.crosshair = QCheckBox("Center crosshair")
        self.crosshair.toggled.connect(self._toggle_crosshair)
        self.to_camera = QPushButton("Set camera ROI to this box")
        self.to_camera.setToolTip("Crop on the camera to the yellow box: less data, higher frame rate.")
        self.to_camera.clicked.connect(self._apply_camera_roi)
        full = QPushButton("Camera full frame")
        full.clicked.connect(controller.full_roi)
        self.rect_label = QLabel("")

        self.table = QTableWidget(7, 2)
        self.table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        top = QHBoxLayout()
        for w in (self.show_roi, self.crosshair):
            top.addWidget(w)
        top.addStretch(1)
        btns = QHBoxLayout()
        btns.addWidget(self.to_camera)
        btns.addWidget(full)
        btns.addStretch(1)
        btns.addWidget(self.rect_label)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(btns)
        lay.addWidget(self.table, 1)

        self.hline = pg.InfiniteLine(angle=0, pen=pg.mkPen((0, 255, 0, 160), width=1))
        self.vline = pg.InfiniteLine(angle=90, pen=pg.mkPen((0, 255, 0, 160), width=1))
        for line in (self.hline, self.vline):
            line.setVisible(False)
            line.setZValue(8)
            view.view.addItem(line)
        self._toggle_roi(False)

    def _toggle_roi(self, on: bool) -> None:
        self.aroi.set_visible(on)
        self.to_camera.setEnabled(on)
        if on and self.ctx.display is not None:
            self.aroi.fit_to(self.ctx.display.shape)

    def _toggle_crosshair(self, on: bool) -> None:
        self.hline.setVisible(on)
        self.vline.setVisible(on)

    def update_view(self) -> None:
        img = self.ctx.display
        if img is None:
            return
        h, w = img.shape[:2]
        self.hline.setPos(h / 2)
        self.vline.setPos(w / 2)
        if self.aroi.visible:
            self.aroi.fit_to(img.shape)
        sl = self.aroi.slices(img.shape)
        if sl is None:
            region, raw = img, self.ctx.raw
            self.rect_label.setText(f"whole image {w} x {h}")
        else:
            region = img[sl]
            raw = self.ctx.raw[sl] if self.ctx.raw is not None and self.ctx.raw.shape[:2] == img.shape[:2] else None
            x, y, rw, rh = self.aroi.rect()
            self.rect_label.setText(f"box x={x} y={y} w={rw} h={rh}")
        stats = region_stats(region, raw, self.ctx.max_value)
        for row, (k, v) in enumerate(stats.rows()):
            self.table.setItem(row, 0, QTableWidgetItem(k))
            self.table.setItem(row, 1, QTableWidgetItem(v))

    def _apply_camera_roi(self) -> None:
        img = self.ctx.display
        c = self.controller
        if img is None or not c.is_open:
            return
        sl = self.aroi.slices(img.shape)
        if sl is None:
            return
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        h, w = img.shape[:2]
        # Displayed pixels are camera pixels of the current ROI (already in
        # binned units); account for any on-camera flips.
        if c.get("ReverseX"):
            x0, x1 = w - x1, w - x0
        if c.get("ReverseY"):
            y0, y1 = h - y1, h - y0
        ox, oy = int(c.get("OffsetX", 0)), int(c.get("OffsetY", 0))
        c.set_roi(ox + x0, oy + y0, x1 - x0, y1 - y0)
        self.show_roi.setChecked(False)


class ProfileTab(QWidget):
    def __init__(self, ctx: AnalysisContext, view: ImageView) -> None:
        super().__init__()
        self.ctx, self.view = ctx, view
        self.enable = QCheckBox("Line profile (blue line on image)")
        self.enable.toggled.connect(self._toggle)
        self.info = QLabel("")
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Distance along line (px)")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.setLabel("left", "Value")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.plot.plot(pen=LINE_PEN)
        self.half_line = pg.InfiniteLine(angle=0, pen=pg.mkPen((200, 200, 200), style=pg.QtCore.Qt.PenStyle.DashLine))
        self.half_line.setVisible(False)
        self.plot.addItem(self.half_line)
        top = QHBoxLayout()
        top.addWidget(self.enable)
        top.addStretch(1)
        top.addWidget(self.info)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.plot, 1)
        self.line = pg.LineSegmentROI([(0, 0), (100, 100)], pen=LINE_PEN)
        self.length = 0.0
        self.line.setZValue(11)
        self.line.setVisible(False)
        view.view.addItem(self.line)
        self._placed_for: tuple[int, int] | None = None

    def _toggle(self, on: bool) -> None:
        self.line.setVisible(on)
        if on:
            self._place()

    def _place(self) -> None:
        img = self.ctx.display
        if img is None:
            return
        h, w = img.shape[:2]
        if self._placed_for != (h, w):
            self.line.setPos((0, 0))
            self.line.movePoint(0, (w * 0.25, h / 2), finish=False)
            self.line.movePoint(1, (w * 0.75, h / 2), finish=False)
            self._placed_for = (h, w)

    def update_view(self) -> None:
        img = self.ctx.display
        if img is None or not self.enable.isChecked():
            return
        self._place()
        data = img if img.ndim == 2 else img.mean(axis=2)
        # LineSegmentROI.getArrayRegion ignores imageAxisOrder="row-major" and
        # samples with x/y swapped, so sample the endpoints ourselves.
        item = self.view.image_item
        p0, p1 = (self.line.mapToItem(item, h.pos()) for h in self.line.getHandles()[:2])
        x, values = sample_line(data, (p0.x(), p0.y()), (p1.x(), p1.y()))
        if values.size < 2:
            return
        length = float(x[-1])
        self.length = length
        self.curve.setData(x, values, connect="finite")
        fwhm = profile_fwhm(x, values)
        if not np.any(np.isfinite(values)):
            self.half_line.setVisible(False)
            self.info.setText(f"length {length:.1f} px   line is outside the image")
            return
        lo, hi = np.nanmin(values), np.nanmax(values)
        self.half_line.setPos((lo + hi) / 2)
        self.half_line.setVisible(fwhm is not None)
        self.info.setText(f"length {length:.1f} px   max {hi:.4g}   "
                          + (f"FWHM {fwhm:.2f} px" if fwhm is not None else "FWHM n/a"))


def sample_line(data: np.ndarray, p0: tuple[float, float],
                p1: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear samples of a 2D (row-major) image along p0 -> p1, given in
    view coordinates where pixel (i, j) spans [j, j+1] x [i, i+1]. Returns
    (distance from p0, values) with about one sample per pixel; points outside
    the image are NaN."""
    (x0, y0), (x1, y1) = p0, p1
    length = float(np.hypot(x1 - x0, y1 - y0))
    n = max(int(np.ceil(length)) + 1, 2)
    t = np.linspace(0.0, 1.0, n)
    cols = x0 + t * (x1 - x0) - 0.5  # pixel centres sit at i + 0.5
    rows = y0 + t * (y1 - y0) - 0.5
    values = map_coordinates(data.astype(np.float64, copy=False), [rows, cols], order=1,
                             mode="nearest")
    h, w = data.shape
    outside = (rows < -0.5) | (rows > h - 0.5) | (cols < -0.5) | (cols > w - 0.5)
    values[outside] = np.nan
    return t * length, values


def profile_fwhm(x: np.ndarray, y: np.ndarray) -> float | None:
    """Full width at half maximum of the main peak (baseline = min), by
    linear interpolation of the half-max crossings."""
    finite = np.flatnonzero(np.isfinite(y))
    if finite.size < 3 or finite[-1] - finite[0] + 1 != finite.size:
        return None  # need one contiguous in-image stretch
    x, y = x[finite[0]:finite[-1] + 1], y[finite[0]:finite[-1] + 1]
    lo, hi = float(y.min()), float(y.max())
    if hi <= lo:
        return None
    half = (lo + hi) / 2
    i = int(np.argmax(y))
    left = i
    while left > 0 and y[left] > half:
        left -= 1
    right = i
    while right < y.size - 1 and y[right] > half:
        right += 1
    if y[left] > half or y[right] > half:
        return None  # peak not fully contained

    def cross(a: int, b: int) -> float:
        return float(x[a] + (half - y[a]) * (x[b] - x[a]) / (y[b] - y[a]))

    return cross(right - 1, right) - cross(left, left + 1)


class AnalysisDock(QTabWidget):
    def __init__(self, controller: CameraController, view: ImageView) -> None:
        super().__init__()
        self.ctx = AnalysisContext()
        self.aroi = AnalysisROI(view)
        self.stats_tab = StatsTab(self.ctx, self.aroi, view, controller)
        self.profile_tab = ProfileTab(self.ctx, view)
        self.addTab(self.stats_tab, "Statistics")
        self.addTab(self.profile_tab, "Line profile")
        self._tabs = [self.stats_tab, self.profile_tab]
        self._dirty = False
        self.aroi.roi.sigRegionChanged.connect(self.request_update)
        self.profile_tab.line.sigRegionChanged.connect(self.request_update)
        self.profile_tab.enable.toggled.connect(self.request_update)
        self.stats_tab.show_roi.toggled.connect(self.request_update)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def add_analysis_tab(self, widget: QWidget, title: str) -> None:
        self.addTab(widget, title)
        self._tabs.append(widget)

    def update_frame(self, display: np.ndarray, raw: np.ndarray, max_value: float, frame_id: int) -> None:
        self.ctx.display, self.ctx.raw = display, raw
        self.ctx.max_value, self.ctx.frame_id = max_value, frame_id
        self._dirty = True

    def request_update(self, *_args) -> None:
        """Re-run analysis on the current frame at the next tick (overlay
        moved, option changed); works while acquisition is stopped."""
        self._dirty = True

    def _tick(self) -> None:
        if not self._dirty or not self.isVisible():
            return
        self._dirty = False
        for tab in self._tabs:
            # Tabs with image overlays update even when not the current tab.
            tab.update_view()
