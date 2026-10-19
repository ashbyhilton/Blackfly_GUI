"""Trigger configuration and software trigger."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from ...camera.controller import CameraController
from ..widgets import FeatureButton, FeatureCombo, FeatureForm, FeatureNumber, FeaturePanel


class TriggerPanel(FeaturePanel):
    def __init__(self, controller: CameraController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent)
        c = controller

        trig = self.add_form(FeatureForm("Trigger"))
        trig.add("Selector", FeatureCombo(c, "TriggerSelector"))
        trig.add("Mode", FeatureCombo(c, "TriggerMode"))
        trig.add("Source", FeatureCombo(c, "TriggerSource"))
        trig.add("Activation", FeatureCombo(c, "TriggerActivation"))
        trig.add("Delay", FeatureNumber(c, "TriggerDelay", decimals=1))
        trig.add("Overlap", FeatureCombo(c, "TriggerOverlap"))
        self.fire = trig.add("", FeatureButton(c, "TriggerSoftware", "Fire software trigger  (T)"))

        help_text = QLabel(
            "<b>Mode Off</b>: free-running at the frame rate.<br>"
            "<b>Mode On</b>: one frame per trigger. Source <i>Software</i> uses the button "
            "above; <i>Line0</i> (opto-isolated input) etc. use an external TTL signal.<br>"
            "Live view keeps running and simply waits for triggers."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: gray;")
        self.layout_.addWidget(help_text)
        self.finish()
