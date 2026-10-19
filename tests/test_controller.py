import pytest

from blackfly_gui.camera import SimulatedCamera
from blackfly_gui.camera.controller import CameraController


@pytest.fixture
def ctrl(qtbot):
    c = CameraController()
    c.open(SimulatedCamera())
    yield c
    c.close()


def test_exposure_resolves_auto(ctrl):
    ctrl.set("ExposureAuto", "Continuous")
    info = ctrl.describe("ExposureTime")
    assert info.settable and "ExposureAuto" in info.note
    assert ctrl.set("ExposureTime", 2000.0)
    assert ctrl.get("ExposureAuto") == "Off"
    assert ctrl.get("ExposureTime") == pytest.approx(2000.0)


def test_trigger_source_restores_trigger_mode(ctrl):
    ctrl.set("TriggerMode", "On")
    assert ctrl.set("TriggerSource", "Line0")
    assert ctrl.get("TriggerSource") == "Line0"
    assert ctrl.get("TriggerMode") == "On"


def test_width_change_while_streaming_restarts(ctrl, qtbot):
    ctrl.start()
    with qtbot.waitSignal(ctrl.frame_ready, timeout=3000):
        pass
    assert "pause" in ctrl.describe("Width").note
    assert ctrl.set("Width", 1024)
    assert ctrl.is_streaming
    frame = None

    def got_small_frame():
        nonlocal frame
        frame = ctrl.last_frame
        assert frame is not None and frame.image.shape[1] == 1024

    qtbot.waitUntil(got_small_frame, timeout=3000)


def test_set_roi_orders_writes(ctrl):
    assert ctrl.set_roi(1500, 1000, 400, 300)
    assert (ctrl.get("OffsetX"), ctrl.get("OffsetY"), ctrl.get("Width"), ctrl.get("Height")) \
        == (1500, 1000, 400, 300)
    # Growing the ROI while offset would overflow must still succeed (offset clamps).
    assert ctrl.set_roi(1500, 1000, 1024, 1024)
    assert ctrl.get("Width") + ctrl.get("OffsetX") <= 2048
    assert ctrl.get("Height") + ctrl.get("OffsetY") <= 1536
    assert ctrl.full_roi()
    assert (ctrl.get("Width"), ctrl.get("Height")) == (2048, 1536)


def test_frame_rate_enable_automatic(ctrl):
    assert ctrl.set("AcquisitionFrameRate", 10.0)
    assert ctrl.get("AcquisitionFrameRateEnable") is True


def test_errors_reported_not_raised(ctrl, qtbot):
    with qtbot.waitSignal(ctrl.message) as blocker:
        assert not ctrl.set("Width", 99999)
    assert "Width" in blocker.args[0]


def test_immediate_start_stop_does_not_leave_thread_spinning(ctrl):
    # Regression: a stop requested before the thread was scheduled used to be
    # overwritten, leaving the thread busy-looping on a stopped camera.
    threads = []
    for _ in range(20):
        ctrl.start()
        threads.append(ctrl._thread)
        ctrl.stop()
    assert all(t.isFinished() for t in threads)
