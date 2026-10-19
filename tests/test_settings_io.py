import json

import pytest

from blackfly_gui import settings_io
from blackfly_gui.camera import SimulatedCamera
from blackfly_gui.camera.controller import CameraController


@pytest.fixture
def ctrl(qtbot):
    c = CameraController()
    c.open(SimulatedCamera())
    yield c
    c.close()


def test_roundtrip(ctrl, tmp_path):
    ctrl.set("BinningHorizontal", 2)
    ctrl.set_roi(100, 50, 256, 200)
    ctrl.set("Gain", 6.0)
    ctrl.set("PixelFormat", "Mono16")
    ctrl.set("TriggerMode", "On")
    ctrl.set("TriggerSource", "Line0")
    path = tmp_path / "s.json"
    n = settings_io.save(ctrl, path)
    assert n > 10
    saved = json.loads(path.read_text())["features"]
    assert "DeviceTemperature" not in saved and "TriggerSoftware" not in saved

    other = CameraController()
    other.open(SimulatedCamera())
    try:
        other.start()
        applied, failed, model = settings_io.load(other, path)
        assert failed == []
        for name in ("BinningHorizontal", "OffsetX", "OffsetY", "Width", "Height", "Gain",
                     "PixelFormat", "TriggerMode", "TriggerSource"):
            assert other.get(name) == ctrl.get(name), name
        assert other.is_streaming
    finally:
        other.close()


def test_auto_locked_values_not_saved(ctrl, tmp_path):
    ctrl.set("ExposureAuto", "Continuous")
    path = tmp_path / "s.json"
    settings_io.save(ctrl, path)
    saved = json.loads(path.read_text())["features"]
    assert saved["ExposureAuto"] == "Continuous"
    assert "ExposureTime" not in saved
