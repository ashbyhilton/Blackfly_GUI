"""Alignment tab: tracks the spot centre and shows how far it is from a saved
reference position, for aligning beams, fibres and MOTs."""

from __future__ import annotations

import math
from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..analysis.alignment import Geometry, spot_centroid
from ..camera.base import DeviceInfo
from ..camera.controller import CameraController
from .analysis import AnalysisContext, AnalysisROI
from .image_view import ImageView
from .worker import BackgroundTask

REF_PEN = pg.mkPen((0, 230, 255), width=2)
SPOT_PEN = pg.mkPen((255, 150, 0), width=2)
LINK_PEN = pg.mkPen((255, 150, 0), width=1, style=Qt.PenStyle.DashLine)
UNSET = -1.0  # reference spin box value meaning "no reference"


class AlignmentTab(QWidget):
    """Positions are kept in full-sensor pixels (unbinned, without ROI offset),
    so the reference stays on the same physical spot when the camera ROI or
    binning changes. The reference is saved per camera serial number."""

    def __init__(self, ctx: AnalysisContext, aroi: AnalysisROI, view: ImageView,
                 controller: CameraController, um_per_sensor_px: Callable[[], float]) -> None:
        super().__init__()
        self.ctx, self.aroi, self.controller = ctx, aroi, controller
        self.um_per_sensor_px = um_per_sensor_px
        self.reference: tuple[float, float] | None = None  # sensor px
        self.current: tuple[float, float] | None = None  # sensor px
        self._geom: Geometry | None = None
        qs = QSettings()

        self.track = QCheckBox("Track spot centre")
        self.track.setToolTip("Intensity-weighted centre of the spot in the analysis ROI (yellow box), "
                              "or the whole image; shown as an orange ×. If a faint spot is not found, "
                              "average a few frames (above the image).")
        self.track.toggled.connect(self._on_track)
        set_ref = QPushButton("Set reference here")
        set_ref.setToolTip("Save the current spot centre as the target (blue ⊕). Kept per camera, "
                           "also after restarting the app.")
        set_ref.clicked.connect(self._set_reference_here)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._set_reference(None))
        self.ref_x, self.ref_y = self._ref_spin(), self._ref_spin()
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 100000.0)
        self.tolerance.setDecimals(1)
        self.tolerance.setSuffix(" px")
        self.tolerance.setValue(float(qs.value("alignment/tolerance_px", 5.0)))
        self.tolerance.setToolTip("The offset turns green when the spot is this close to the reference "
                                  "(sensor pixels).")
        self.tolerance.valueChanged.connect(self._on_tolerance)

        self.readout = QLabel("")
        self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: gray;")

        grid = QGridLayout()
        grid.addWidget(self.track, 0, 0, 1, 2)
        grid.addWidget(QLabel("Reference x"), 1, 0)
        grid.addWidget(self.ref_x, 1, 1)
        grid.addWidget(QLabel("y"), 1, 2)
        grid.addWidget(self.ref_y, 1, 3)
        grid.addWidget(set_ref, 1, 4)
        grid.addWidget(clear, 1, 5)
        grid.addWidget(QLabel("Tolerance"), 2, 0)
        grid.addWidget(self.tolerance, 2, 1)
        grid.setColumnStretch(6, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(self.readout)
        lay.addWidget(self.detail)
        lay.addStretch(1)

        self.ref_marker = pg.ScatterPlotItem(size=30, pen=REF_PEN, brush=None)
        self.spot_marker = pg.ScatterPlotItem(size=14, symbol="x", pen=SPOT_PEN, brush=None)
        self.link = pg.PlotCurveItem(pen=LINK_PEN)
        for item in (self.link, self.ref_marker, self.spot_marker):
            item.setZValue(13)
            view.view.addItem(item)

        self.task = BackgroundTask(self._on_centroid, self)
        controller.opened.connect(self._load_reference)
        controller.closed.connect(self._on_closed)

    def _ref_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(UNSET, 100000.0)
        spin.setDecimals(1)
        spin.setSuffix(" px")
        spin.setSpecialValueText("—")
        spin.setValue(UNSET)
        spin.setKeyboardTracking(False)
        spin.setToolTip("Reference position in full-sensor pixels (unbinned, without ROI offset). "
                        "Type a value to set it by hand.")
        spin.valueChanged.connect(self._on_ref_edited)
        return spin

    # ------------------------------------------------------ reference
    def _key(self) -> str | None:
        dev = self.controller.device
        return f"alignment/{dev.serial}" if dev else None

    def _set_reference(self, ref: tuple[float, float] | None) -> None:
        self.reference = ref
        for spin, v in zip((self.ref_x, self.ref_y), ref or (UNSET, UNSET)):
            spin.blockSignals(True)
            spin.setValue(v)
            spin.blockSignals(False)
        key = self._key()
        if key:
            qs = QSettings()
            if ref is None:
                qs.remove(key)
            else:
                qs.setValue(f"{key}/x", ref[0])
                qs.setValue(f"{key}/y", ref[1])
        self._show()

    def _set_reference_here(self) -> None:
        if self.current is None:
            self.controller.message.emit("No spot to use as the reference (tick Track spot centre)")
            return
        self._set_reference(self.current)

    def _on_ref_edited(self) -> None:
        x, y = self.ref_x.value(), self.ref_y.value()
        self._set_reference((x, y) if x > UNSET and y > UNSET else None)

    def _load_reference(self, dev: DeviceInfo) -> None:
        qs = QSettings()
        x, y = qs.value(f"alignment/{dev.serial}/x"), qs.value(f"alignment/{dev.serial}/y")
        self._set_reference((float(x), float(y)) if x is not None and y is not None else None)

    def _on_tolerance(self, value: float) -> None:
        QSettings().setValue("alignment/tolerance_px", value)
        self._show()

    def _on_closed(self) -> None:
        self.current = self._geom = None
        self._show()

    # ------------------------------------------------------ tracking
    def _on_track(self, on: bool) -> None:
        if on:
            self.update_view()
        else:
            self.current = None
        self._show()

    def _geometry(self, shape: tuple[int, ...]) -> Geometry:
        c = self.controller
        return Geometry(
            width=shape[1], height=shape[0],
            offset_x=int(c.get("OffsetX", 0) or 0), offset_y=int(c.get("OffsetY", 0) or 0),
            step_x=int(c.get("BinningHorizontal", 1) or 1) * int(c.get("DecimationHorizontal", 1) or 1),
            step_y=int(c.get("BinningVertical", 1) or 1) * int(c.get("DecimationVertical", 1) or 1),
            reverse_x=bool(c.get("ReverseX", False)), reverse_y=bool(c.get("ReverseY", False)),
        )

    # called at 10 Hz by the dock
    def update_view(self) -> None:
        img = self.ctx.display
        if img is None or not self.track.isChecked():
            return
        sl = self.aroi.slices(img.shape)
        region, x_off, y_off = (img, 0, 0) if sl is None else (img[sl], sl[1].start, sl[0].start)
        # Camera state is read here, on the GUI thread; only numpy runs in the worker.
        self.task.submit(lambda: spot_centroid(region), (self._geometry(img.shape), x_off, y_off))

    def _on_centroid(self, c: tuple[float, float] | None | Exception, context: tuple) -> None:
        if not self.track.isChecked() or not self.controller.is_open:
            return
        self._geom, x_off, y_off = context
        if isinstance(c, Exception):
            c = None
        # Pixel i spans [i, i+1] in view coordinates, so its centre is i + 0.5.
        self.current = None if c is None else self._geom.to_sensor(c[0] + x_off + 0.5, c[1] + y_off + 0.5)
        self._show()

    def _show(self) -> None:
        g = self._geom
        on = self.track.isChecked() and g is not None
        ref = g.to_image(*self.reference) if on and self.reference is not None else None
        spot = g.to_image(*self.current) if on and self.current is not None else None
        if ref:
            self.ref_marker.setData([ref[0]] * 2, [ref[1]] * 2, symbol=["o", "+"])
        else:
            self.ref_marker.setData([], [])
        self.spot_marker.setData([spot[0]] if spot else [], [spot[1]] if spot else [])
        if ref and spot:
            self.link.setData([ref[0], spot[0]], [ref[1], spot[1]])
        else:
            self.link.setData([], [])

        if not on:
            self.readout.setText("")
            self.detail.setText("")
            return
        if spot is None:
            self._readout("No spot found", "gray")
            self.detail.setText("")
            return
        assert g is not None and self.current is not None
        self.detail.setText(f"Spot centre x = {self.current[0]:.1f}, y = {self.current[1]:.1f} "
                            "(sensor px). Δ is measured on screen: +x right, +y down.")
        if ref is None:
            self._readout("Set a reference to see the offset", "gray")
            return
        # On-screen direction (flips included), in sensor pixels.
        dx, dy = (spot[0] - ref[0]) * g.step_x, (spot[1] - ref[1]) * g.step_y
        d, um = math.hypot(dx, dy), self.um_per_sensor_px()
        colour = "#30b030" if d <= self.tolerance.value() else "#e08000"
        self._readout(f"Δx {dx:+.1f} px ({dx * um:+.1f} µm)    Δy {dy:+.1f} px ({dy * um:+.1f} µm)"
                      f"    |Δ| {d:.1f} px ({d * um:.1f} µm)", colour)

    def _readout(self, text: str, colour: str) -> None:
        # Large, so it can be read from the optical table while adjusting mirrors.
        self.readout.setStyleSheet(f"font-size: 18pt; font-weight: bold; color: {colour};")
        self.readout.setText(text)
