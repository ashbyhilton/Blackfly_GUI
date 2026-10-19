"""Camera user sets and settings files."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ... import settings_io
from ...camera.controller import CameraController
from ..widgets import FeatureButton, FeatureCombo, FeatureForm, FeaturePanel


class UserSetsPanel(FeaturePanel):
    def __init__(self, controller: CameraController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent, poll_ms=0)
        c = controller

        us = self.add_form(FeatureForm("Camera user sets (stored in the camera)"))
        us.add("User set", FeatureCombo(c, "UserSetSelector"))
        btns = QWidget()
        row = QHBoxLayout(btns)
        row.setContentsMargins(0, 0, 0, 0)
        load = FeatureButton(c, "UserSetLoad", "Load")
        save = FeatureButton(c, "UserSetSave", "Save current")
        row.addWidget(load)
        row.addWidget(save)
        us.widgets.extend([load, save])
        us.add_row("", btns)
        us.add("Load at power-up", FeatureCombo(c, "UserSetDefault"))
        factory = QPushButton("Restore factory defaults")
        factory.setToolTip("Load the read-only 'Default' user set.")
        factory.clicked.connect(self._factory_defaults)
        us.add_row("", factory)

        files = FeatureForm("Settings files (share with others)")
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        save_f = QPushButton("Save to file…")
        save_f.clicked.connect(self._save_file)
        load_f = QPushButton("Load from file…")
        load_f.clicked.connect(self._load_file)
        row.addWidget(save_f)
        row.addWidget(load_f)
        files.add_row("", row_w)
        hint = QLabel("A JSON file of every writable camera setting. Load it on any "
                      "camera of the same model to reproduce a configuration.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        files.add_row(hint)
        self.add_form(files)
        self.finish()

    def _factory_defaults(self) -> None:
        c = self.controller
        if QMessageBox.question(self, "Restore factory defaults",
                                "Load the camera's factory default settings?") \
                != QMessageBox.StandardButton.Yes:
            return
        if c.set("UserSetSelector", "Default"):
            c.execute("UserSetLoad")

    def _save_file(self) -> None:
        qs = QSettings()
        path, _ = QFileDialog.getSaveFileName(self, "Save camera settings",
                                              qs.value("settings_dir", ""), "Camera settings (*.json)")
        if not path:
            return
        n = settings_io.save(self.controller, path)
        qs.setValue("settings_dir", path)
        self.controller.message.emit(f"Saved {n} settings to {path}")

    def _load_file(self) -> None:
        qs = QSettings()
        path, _ = QFileDialog.getOpenFileName(self, "Load camera settings",
                                              qs.value("settings_dir", ""), "Camera settings (*.json)")
        if not path:
            return
        qs.setValue("settings_dir", path)
        try:
            applied, failed, model = settings_io.load(self.controller, path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not load settings", str(exc))
            return
        dev = self.controller.device
        msg = f"Applied {applied} settings from {path}."
        if model and dev and model != dev.model:
            msg += f"\nNote: file was saved from a {model}."
        if failed:
            msg += f"\n{len(failed)} could not be applied: {', '.join(failed[:12])}" + \
                   ("…" if len(failed) > 12 else "")
            QMessageBox.warning(self, "Settings loaded with warnings", msg)
        self.controller.message.emit(msg.splitlines()[0])
