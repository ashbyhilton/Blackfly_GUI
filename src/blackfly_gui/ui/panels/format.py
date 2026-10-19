"""Pixel format, binning, decimation, ROI, orientation."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ...camera.controller import CameraController
from ..widgets import FeatureCheck, FeatureCombo, FeatureForm, FeatureNumber, FeaturePanel, FeatureReadout


class RoiNumber(FeatureNumber):
    """Width/Height/OffsetX/OffsetY: applied together via set_roi so that
    e.g. enlarging Width automatically reduces OffsetX instead of failing."""

    def apply(self, value: Any) -> None:
        if self._updating:
            return
        c = self.controller
        roi = {n: int(c.get(n, 0)) for n in ("OffsetX", "OffsetY", "Width", "Height")}
        roi[self.name] = int(value)
        c.set_roi(roi["OffsetX"], roi["OffsetY"], roi["Width"], roi["Height"])


class FormatPanel(FeaturePanel):
    def __init__(self, controller: CameraController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent, poll_ms=0)
        c = controller

        fmt = self.add_form(FeatureForm("Pixel format"))
        fmt.add("Format", FeatureCombo(c, "PixelFormat"))
        fmt.add("ADC bit depth", FeatureCombo(c, "AdcBitDepth"))
        fmt.add("Sensor width", FeatureReadout(c, "SensorWidth", "{:d}"))
        fmt.add("Sensor height", FeatureReadout(c, "SensorHeight", "{:d}"))

        binf = self.add_form(FeatureForm("Binning (on camera)"))
        quick = QWidget()
        row = QHBoxLayout(quick)
        row.setContentsMargins(0, 0, 0, 0)
        for n in (1, 2, 4):
            b = QPushButton(f"{n}×{n}")
            b.setToolTip(f"Set horizontal and vertical binning to {n}. Resets the ROI to full frame.")
            b.clicked.connect(lambda _=False, n=n: self._set_binning(n))
            row.addWidget(b)
        binf.add_row("Quick", quick)
        binf.add("Selector", FeatureCombo(c, "BinningSelector"))
        binf.add("Horizontal", FeatureNumber(c, "BinningHorizontal"))
        binf.add("Vertical", FeatureNumber(c, "BinningVertical"))
        binf.add("H mode", FeatureCombo(c, "BinningHorizontalMode"))
        binf.add("V mode", FeatureCombo(c, "BinningVerticalMode"))

        dec = self.add_form(FeatureForm("Decimation"))
        dec.add("Horizontal", FeatureNumber(c, "DecimationHorizontal"))
        dec.add("Vertical", FeatureNumber(c, "DecimationVertical"))

        roi = self.add_form(FeatureForm("Region of interest"))
        roi.add("Width", RoiNumber(c, "Width"))
        roi.add("Height", RoiNumber(c, "Height"))
        roi.add("Offset X", RoiNumber(c, "OffsetX"))
        roi.add("Offset Y", RoiNumber(c, "OffsetY"))
        btns = QWidget()
        row = QHBoxLayout(btns)
        row.setContentsMargins(0, 0, 0, 0)
        center = QPushButton("Center")
        center.setToolTip("Keep the ROI size and center it on the sensor.")
        center.clicked.connect(self._center_roi)
        full = QPushButton("Full frame")
        full.clicked.connect(c.full_roi)
        row.addWidget(center)
        row.addWidget(full)
        roi.add_row("", btns)

        orient = self.add_form(FeatureForm("Orientation"))
        orient.add("Flip", FeatureCheck(c, "ReverseX", "Reverse X"))
        orient.add("", FeatureCheck(c, "ReverseY", "Reverse Y"))

        self.finish()

    def _set_binning(self, n: int) -> None:
        c = self.controller
        with c.paused():
            for name in ("BinningHorizontal", "BinningVertical"):
                info = c.describe(name)
                if info is not None and info.settable and info.value != n:
                    c.set(name, n)
        c.full_roi()

    def _center_roi(self) -> None:
        c = self.controller
        w, h = int(c.get("Width", 0)), int(c.get("Height", 0))
        wmax = int(c.get("WidthMax") or c.get("SensorWidth") or w)
        hmax = int(c.get("HeightMax") or c.get("SensorHeight") or h)
        c.set_roi((wmax - w) // 2, (hmax - h) // 2, w, h)
