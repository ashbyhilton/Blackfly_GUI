"""Saving to a location that cannot be written must show an error, not fail
silently."""

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox

from blackfly_gui.camera import SimulatedCamera
from blackfly_gui.camera.controller import CameraController
from blackfly_gui.ui.analysis import AnalysisContext
from blackfly_gui.ui.analysis_tools import ReferenceTab
from blackfly_gui.ui.panels.usersets import UserSetsPanel


@pytest.fixture
def ctrl(qtbot, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    c = CameraController()
    c.open(SimulatedCamera())
    yield c
    c.close()


@pytest.fixture
def errors(monkeypatch, tmp_path):
    """Make every save dialog pick a file in a folder that does not exist and
    record the error boxes shown."""
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, title, _text: shown.append(title))
    path = str(tmp_path / "no_such_folder" / "file")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (path, ""))
    return shown


def test_settings_file_save_error_is_shown(ctrl, qtbot, errors):
    panel = UserSetsPanel(ctrl)
    qtbot.addWidget(panel)
    panel._save_file()
    assert errors == ["Could not save settings"]


def test_reference_image_save_error_is_shown(ctrl, qtbot, errors):
    tab = ReferenceTab(AnalysisContext(), ctrl)
    qtbot.addWidget(tab)
    tab.manager.images["dark"] = np.zeros((4, 4), dtype=np.float32)
    tab._save("dark")
    assert errors == ["Could not save image"]
