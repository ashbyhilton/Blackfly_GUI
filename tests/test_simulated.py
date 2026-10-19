import numpy as np
import pytest

from blackfly_gui.camera import CameraError, SimulatedCamera


@pytest.fixture
def cam():
    c = SimulatedCamera()
    c.open()
    yield c
    c.close()


def grab_one(cam, timeout_ms=2000):
    cam.start()
    try:
        return cam.grab(timeout_ms)
    finally:
        cam.stop()


def test_default_frame(cam):
    f = grab_one(cam)
    assert f is not None
    assert f.image.shape == (1536, 2048)
    assert f.image.dtype == np.uint8
    assert f.max_value == 255


def test_roi_locked_while_streaming(cam):
    cam.start()
    with pytest.raises(CameraError, match="streaming"):
        cam.set_value("Width", 1024)
    assert cam.get_info("Width").writable is False
    cam.stop()
    cam.set_value("Width", 1024)
    assert cam.get_value("Width") == 1024


def test_exposure_needs_auto_off(cam):
    cam.set_value("ExposureAuto", "Continuous")
    with pytest.raises(CameraError, match="ExposureAuto"):
        cam.set_value("ExposureTime", 5000)


def test_width_increment_enforced(cam):
    with pytest.raises(CameraError, match="multiple"):
        cam.set_value("Width", 1001)


def test_binning_resets_roi_and_shrinks_image(cam):
    cam.set_value("Width", 512)
    cam.set_value("BinningHorizontal", 2)
    cam.set_value("BinningVertical", 2)
    assert cam.get_value("Width") == 1024
    assert cam.get_value("Height") == 768
    f = grab_one(cam)
    assert f.image.shape == (768, 1024)


def test_pixel_formats(cam):
    for fmt, maxv in [("Mono12p", 4095), ("Mono16", 65535)]:
        cam.set_value("PixelFormat", fmt)
        f = grab_one(cam)
        assert f.image.dtype == np.uint16
        assert f.max_value == maxv
        assert f.image.max() <= maxv


def test_software_trigger(cam):
    cam.set_value("TriggerMode", "On")
    cam.start()
    try:
        assert cam.grab(150) is None  # no trigger yet
        cam.execute("TriggerSoftware")
        assert cam.grab(1000) is not None
    finally:
        cam.stop()


def test_user_set_roundtrip(cam):
    cam.set_value("Gain", 12.0)
    cam.set_value("UserSetSelector", "UserSet0")
    cam.execute("UserSetSave")
    cam.set_value("Gain", 0.0)
    cam.execute("UserSetLoad")
    assert cam.get_value("Gain") == 12.0
