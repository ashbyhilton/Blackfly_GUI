"""Gaussian-fit and reference-correction tabs of the analysis dock."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analysis.gaussian import FWHM_PER_SIGMA, GaussianFit, fit_gaussian_2d
from ..analysis.reference import MODES, ReferenceManager
from ..camera.controller import CameraController
from .analysis import AnalysisContext, AnalysisROI
from .image_view import ImageView

ELLIPSE_PEN = pg.mkPen((255, 80, 255), width=2)


# ---------------------------------------------------------------- Gaussian
class _FitSignals(QObject):
    done = Signal(object, int, int)  # GaussianFit, x offset, y offset


class _FitJob(QRunnable):
    def __init__(self, region: np.ndarray, x_off: int, y_off: int, saturation: float | None,
                 signals: _FitSignals) -> None:
        super().__init__()
        self.region, self.x_off, self.y_off, self.saturation = region, x_off, y_off, saturation
        # A long-lived signals object owned by the GUI: a per-job QObject is
        # destroyed with the auto-deleted job before the queued signal is
        # delivered, silently dropping the result.
        self.signals = signals

    def run(self) -> None:
        try:
            result = fit_gaussian_2d(self.region, saturation=self.saturation)
        except Exception as exc:  # noqa: BLE001 - never kill the worker thread
            result = GaussianFit(False, f"Fit error: {exc}")
        self.signals.done.emit(result, self.x_off, self.y_off)


class GaussianTab(QWidget):
    """Live 2D Gaussian fit in the analysis ROI (or the whole image)."""

    ROWS = ["Centre x", "Centre y", "σ major", "σ minor", "FWHM major", "FWHM minor",
            "σ x", "σ y", "FWHM x", "FWHM y", "1/e² diameter major", "1/e² diameter minor",
            "Angle (°)", "Ellipticity", "Amplitude", "Offset", "RMS residual"]

    def __init__(self, ctx: AnalysisContext, aroi: AnalysisROI, view: ImageView,
                 controller: CameraController) -> None:
        super().__init__()
        self.ctx, self.aroi, self.view, self.controller = ctx, aroi, view, controller
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._fit_signals = _FitSignals(self)
        self._fit_signals.done.connect(self._on_done)
        self._raw_saturated = False
        self._busy = False
        self._last_fit_frame = -1
        self.result: GaussianFit | None = None
        self._result_offset = (0, 0)
        qs = QSettings()

        self.enable = QCheckBox("Fit 2D Gaussian")
        self.enable.setToolTip("Fits the analysis ROI (yellow box) if shown, else the whole image.")
        self.show_ellipse = QCheckBox("Show FWHM ellipse")
        self.show_ellipse.setChecked(True)
        self.pixel_size = QDoubleSpinBox()
        self.pixel_size.setRange(0.01, 1000)
        self.pixel_size.setDecimals(3)
        self.pixel_size.setSuffix(" µm")
        self.pixel_size.setValue(float(qs.value("pixel_size_um", 3.45)))
        self.pixel_size.setToolTip("Physical sensor pixel pitch (IMX265 / BFS-U3-31S4M: 3.45 µm).")
        self.magnification = QDoubleSpinBox()
        self.magnification.setRange(0.001, 10000)
        self.magnification.setDecimals(3)
        self.magnification.setValue(float(qs.value("magnification", 1.0)))
        self.magnification.setToolTip("Optical magnification. Object-space size = image size / M.")
        self.include_binning = QCheckBox("× binning/decimation")
        self.include_binning.setChecked(True)
        self.include_binning.setToolTip("Multiply the pixel size by the current on-camera binning and "
                                        "decimation factors (displayed pixels are then larger).")
        for w in (self.pixel_size, self.magnification):
            w.valueChanged.connect(self._save_settings)
            w.valueChanged.connect(self._show_result)
        self.include_binning.toggled.connect(self._show_result)
        copy = QPushButton("Copy results")
        copy.setToolTip("Copy the table to the clipboard (tab separated, pastes into Excel).")
        copy.clicked.connect(self._copy)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.table = QTableWidget(len(self.ROWS), 3)
        self.table.setHorizontalHeaderLabels(["Parameter", "pixels", "µm"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, name in enumerate(self.ROWS):
            self.table.setItem(r, 0, QTableWidgetItem(name))

        controls = QGridLayout()
        controls.addWidget(self.enable, 0, 0)
        controls.addWidget(self.show_ellipse, 0, 1)
        controls.addWidget(copy, 0, 2)
        controls.addWidget(QLabel("Pixel size"), 1, 0)
        controls.addWidget(self.pixel_size, 1, 1)
        controls.addWidget(self.include_binning, 1, 2)
        controls.addWidget(QLabel("Magnification"), 2, 0)
        controls.addWidget(self.magnification, 2, 1)
        left = QVBoxLayout()
        left.addLayout(controls)
        left.addWidget(self.status)
        left.addStretch(1)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.table, 2)

        # Overlay: FWHM ellipse + centre marker.
        self.ellipse = pg.QtWidgets.QGraphicsEllipseItem()
        self.ellipse.setPen(ELLIPSE_PEN)
        self.ellipse.setZValue(12)
        self.ellipse.setVisible(False)
        view.view.addItem(self.ellipse)
        self.centre = pg.ScatterPlotItem(size=12, symbol="+", pen=ELLIPSE_PEN, brush=None)
        self.centre.setZValue(12)
        view.view.addItem(self.centre)
        self.enable.toggled.connect(self._on_enable)
        self.show_ellipse.toggled.connect(lambda _on: self._update_overlay())

    def _save_settings(self) -> None:
        qs = QSettings()
        qs.setValue("pixel_size_um", self.pixel_size.value())
        qs.setValue("magnification", self.magnification.value())

    def _on_enable(self, on: bool) -> None:
        if not on:
            self.result = None
            self._update_overlay()
            self.status.setText("")

    # called at 10 Hz by the dock
    def update_view(self) -> None:
        img = self.ctx.display
        if not self.enable.isChecked() or img is None or self._busy:
            return
        if self.ctx.frame_id == self._last_fit_frame:
            return
        sl = self.aroi.slices(img.shape)
        if sl is None:
            region, x_off, y_off = img, 0, 0
            raw = self.ctx.raw
        else:
            region, x_off, y_off = img[sl], sl[1].start, sl[0].start
            raw = self.ctx.raw[sl] if self.ctx.raw is not None and self.ctx.raw.shape[:2] == img.shape[:2] else None
        # Saturation is a property of the raw camera data. For an uncorrected
        # (integer) image the fit checks it itself; for a reference-corrected
        # (float) image we check the raw pixels here.
        if np.issubdtype(region.dtype, np.integer):
            saturation, sat_note = self.ctx.max_value, False
        else:
            saturation = None
            sat_note = raw is not None and bool(np.count_nonzero(raw >= self.ctx.max_value))
        job = _FitJob(np.array(region, copy=True), x_off, y_off, saturation, self._fit_signals)
        self._raw_saturated = sat_note
        self._busy = True
        self._last_fit_frame = self.ctx.frame_id
        self.pool.start(job)

    def _on_done(self, result: GaussianFit, x_off: int, y_off: int) -> None:
        self._busy = False
        if self._raw_saturated:
            result.warnings.append("Saturated pixels in the raw image: fit is biased")
        if not self.enable.isChecked():
            return
        self.result = result
        self._result_offset = (x_off, y_off)
        self._show_result()
        self._update_overlay()

    def _binning_factors(self) -> tuple[int, int]:
        c = self.controller
        bh = int(c.get("BinningHorizontal", 1) or 1) * int(c.get("DecimationHorizontal", 1) or 1)
        bv = int(c.get("BinningVertical", 1) or 1) * int(c.get("DecimationVertical", 1) or 1)
        return bh, bv

    def scale_um(self) -> float:
        """µm per displayed pixel (horizontal factor if H/V binning differ)."""
        px = self.pixel_size.value() / self.magnification.value()
        if self.include_binning.isChecked():
            px *= self._binning_factors()[0]
        return px

    def _show_result(self) -> None:
        r = self.result
        if r is None:
            return
        if not r.success:
            self.status.setText(f"<span style='color:#d05050'>{r.message}</span>")
            for row in range(len(self.ROWS)):
                for col in (1, 2):
                    self.table.setItem(row, col, QTableWidgetItem("—"))
            return
        x_off, y_off = self._result_offset
        s = self.scale_um()
        F = FWHM_PER_SIGMA
        values = [
            (r.x0 + x_off, True), (r.y0 + y_off, True),
            (r.sigma_major, True), (r.sigma_minor, True),
            (F * r.sigma_major, True), (F * r.sigma_minor, True),
            (r.sigma_x, True), (r.sigma_y, True), (F * r.sigma_x, True), (F * r.sigma_y, True),
            (4 * r.sigma_major, True), (4 * r.sigma_minor, True),
            (r.theta_deg, False), (r.ellipticity, False),
            (r.amplitude, False), (r.offset, False), (r.rms_residual, False),
        ]
        for row, (v, spatial) in enumerate(values):
            self.table.setItem(row, 1, QTableWidgetItem(f"{v:.4g}" if row >= 12 else f"{v:.2f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{v * s:.2f}" if spatial else ""))
        text = f"Fit OK in {r.fit_time_s * 1000:.0f} ms. 1 px = {s:.3f} µm."
        bh, bv = self._binning_factors()
        if self.include_binning.isChecked() and bh != bv:
            text += "<br>Note: unequal H/V binning; µm values use the horizontal factor."
        if r.warnings:
            text += "".join(f"<br><span style='color:#d08000'>⚠ {w}</span>" for w in r.warnings)
        self.status.setText(text)

    def _update_overlay(self) -> None:
        r = self.result
        show = self.enable.isChecked() and r is not None and r.success
        self.ellipse.setVisible(bool(show and self.show_ellipse.isChecked()))
        if not show:
            self.centre.setData([])
            return
        x_off, y_off = self._result_offset
        # Pixel i spans [i, i+1] in view coordinates, so its centre is i + 0.5.
        cx, cy = r.x0 + x_off + 0.5, r.y0 + y_off + 0.5
        a, b = FWHM_PER_SIGMA * r.sigma_major / 2, FWHM_PER_SIGMA * r.sigma_minor / 2
        self.ellipse.setRect(-a, -b, 2 * a, 2 * b)
        self.ellipse.setPos(cx, cy)
        self.ellipse.setRotation(r.theta_deg)
        self.centre.setData([cx], [cy])

    def _copy(self) -> None:
        lines = ["Parameter\tpixels\tum"]
        for row in range(self.table.rowCount()):
            cells = [self.table.item(row, c).text() if self.table.item(row, c) else "" for c in range(3)]
            lines.append("\t".join(cells))
        lines.append(f"Pixel size (um)\t{self.scale_um():.4f}")
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.controller.message.emit("Gaussian fit results copied to clipboard")


# --------------------------------------------------------------- Reference
class ReferenceTab(QWidget):
    """Capture/load dark and reference images and choose a correction."""

    def __init__(self, ctx: AnalysisContext, controller: CameraController) -> None:
        super().__init__()
        self.ctx, self.controller = ctx, controller
        self.manager = ReferenceManager()

        self.mode = QComboBox()
        for key, label in MODES.items():
            self.mode.addItem(label, key)
        self.mode.currentIndexChanged.connect(self._on_mode)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #d08000;")

        top = QFormLayout()
        top.addRow("Display", self.mode)
        top.addRow(self.warning)

        row = QHBoxLayout()
        self.boxes: dict[str, dict] = {}
        for target, title, hint in (
            ("dark", "Dark (D)", "Block all light (lens cap) with the same exposure/gain."),
            ("ref", "Reference / flat (R)", "Background scene, or uniform illumination for flat-field."),
        ):
            row.addWidget(self._make_box(target, title, hint))
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(row)
        lay.addStretch(1)

    def _make_box(self, target: str, title: str, hint: str) -> QGroupBox:
        box = QGroupBox(title)
        frames = QSpinBox()
        frames.setRange(1, 1000)
        frames.setValue(16)
        frames.setSuffix(" frames")
        frames.setToolTip("Number of frames to average (noise drops by √N).")
        capture = QPushButton("Capture")
        capture.clicked.connect(lambda: self._capture(target, frames.value()))
        save = QPushButton("Save…")
        save.clicked.connect(lambda: self._save(target))
        load = QPushButton("Load…")
        load.clicked.connect(lambda: self._load(target))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._clear(target))
        status = QLabel("none")
        status.setWordWrap(True)
        hint_l = QLabel(hint)
        hint_l.setWordWrap(True)
        hint_l.setStyleSheet("color: gray;")
        g = QGridLayout(box)
        g.addWidget(frames, 0, 0)
        g.addWidget(capture, 0, 1)
        g.addWidget(save, 1, 0)
        g.addWidget(load, 1, 1)
        g.addWidget(clear, 1, 2)
        g.addWidget(status, 2, 0, 1, 3)
        g.addWidget(hint_l, 3, 0, 1, 3)
        self.boxes[target] = {"status": status, "capture": capture}
        return box

    # main window calls this for every raw frame (before display)
    def process(self, image: np.ndarray) -> np.ndarray:
        done = self.manager.feed(image)
        if done is not None:
            self._capture_finished(done)
        return self.manager.apply(image)

    def update_view(self) -> None:
        cap = self.manager.capture
        for target, w in self.boxes.items():
            if cap is not None and cap.target == target:
                w["status"].setText(f"capturing… {cap.count}/{cap.n}")
        self.warning.setText(self.manager.warning)

    def _on_mode(self) -> None:
        key = self.mode.currentData()
        self.manager.mode = key
        if key != "off" and not self.manager.ready(key):
            self.manager.warning = "Capture or load the required reference image(s) first"
        self.update_view()

    def _capture(self, target: str, n: int) -> None:
        if not self.controller.is_streaming:
            QMessageBox.information(self, "Not acquiring", "Start acquisition first (Space).")
            return
        self.manager.start_capture(target, n)
        self.boxes[target]["status"].setText(f"capturing… 0/{n}")

    def _capture_finished(self, target: str) -> None:
        self._describe(target)
        self.controller.message.emit(f"{'Dark' if target == 'dark' else 'Reference'} image captured")

    def _describe(self, target: str) -> None:
        img = self.manager.images[target]
        label = self.boxes[target]["status"]
        if img is None:
            label.setText("none")
        else:
            label.setText(f"{img.shape[1]} x {img.shape[0]}, mean {float(np.mean(img)):.4g}, "
                          f"min {float(np.min(img)):.4g}, max {float(np.max(img)):.4g}")

    def _save(self, target: str) -> None:
        if self.manager.images[target] is None:
            return
        qs = QSettings()
        path, _ = QFileDialog.getSaveFileName(self, "Save reference image",
                                              str(Path(qs.value("ref_dir", "")) / f"{target}.tif"),
                                              "TIFF (*.tif *.tiff)")
        if path:
            self.manager.save(target, path)
            qs.setValue("ref_dir", str(Path(path).parent))

    def _load(self, target: str) -> None:
        qs = QSettings()
        path, _ = QFileDialog.getOpenFileName(self, "Load reference image", qs.value("ref_dir", ""),
                                              "TIFF (*.tif *.tiff)")
        if not path:
            return
        try:
            self.manager.load(target, path)
        except Exception as exc:  # noqa: BLE001 - show any file error
            QMessageBox.critical(self, "Could not load image", str(exc))
            return
        qs.setValue("ref_dir", str(Path(path).parent))
        self._describe(target)

    def _clear(self, target: str) -> None:
        self.manager.clear(target)
        self._describe(target)
