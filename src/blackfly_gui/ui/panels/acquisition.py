"""Exposure, gain, frame rate."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ...camera.controller import CameraController
from ..widgets import FeatureCheck, FeatureCombo, FeatureForm, FeatureNumber, FeaturePanel, FeatureReadout


class AcquisitionPanel(FeaturePanel):
    def __init__(self, controller: CameraController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent)
        c = controller

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        snap = QPushButton("Single frame")
        snap.setToolTip("Acquire exactly one frame (or fire a software trigger if live).")
        snap.clicked.connect(c.snap)
        row.addWidget(snap)
        self.layout_.addWidget(buttons)

        exp = self.add_form(FeatureForm("Exposure"))
        exp.add("Auto", FeatureCombo(c, "ExposureAuto"))
        exp.add("Exposure", FeatureNumber(c, "ExposureTime", decimals=1, slider=True, log=True))
        exp.add("Mode", FeatureCombo(c, "ExposureMode"))

        gain = self.add_form(FeatureForm("Gain && levels"))
        gain.add("Auto gain", FeatureCombo(c, "GainAuto"))
        gain.add("Gain", FeatureNumber(c, "Gain", decimals=2, slider=True))
        gain.add("Black level", FeatureNumber(c, "BlackLevel", decimals=2, slider=True))
        gain.add("Gamma", FeatureCheck(c, "GammaEnable", "Enable"))
        gain.add("", FeatureNumber(c, "Gamma", decimals=2, slider=True))
        gain.add("White balance", FeatureCombo(c, "BalanceWhiteAuto"))

        fr = self.add_form(FeatureForm("Frame rate"))
        fr.add("Limit", FeatureCheck(c, "AcquisitionFrameRateEnable", "Limit frame rate"))
        fr.add("Target", FeatureNumber(c, "AcquisitionFrameRate", decimals=2, slider=True))
        fr.add("Resulting", FeatureReadout(c, "AcquisitionResultingFrameRate", "{:.2f}"))
        fr.add("Link limit", FeatureNumber(c, "DeviceLinkThroughputLimit"))

        self.finish()
