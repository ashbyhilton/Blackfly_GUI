"""CameraController: the single object the GUI talks to.

Responsibilities
----------------
* Owns the backend and a low-latency acquisition thread.
* Resolves the GenICam dependencies that make features appear "locked"
  (e.g. ExposureTime needs ExposureAuto=Off, ROI changes need the stream
  stopped) so that the user simply sets what they want.
* Notifies panels when anything may have changed so they can refresh
  values and ranges (changing exposure changes the max frame rate, changing
  binning changes the ROI, ...).
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .base import CameraBackend, CameraError, DeviceInfo, FeatureInfo, Frame

# feature -> [(prerequisite feature, required value, restore afterwards?)]
PREREQUISITES: dict[str, list[tuple[str, Any, bool]]] = {
    "ExposureTime": [("ExposureAuto", "Off", False)],
    "Gain": [("GainAuto", "Off", False)],
    "AcquisitionFrameRate": [("AcquisitionFrameRateEnable", True, False)],
    "Gamma": [("GammaEnable", True, False)],
    "BalanceRatio": [("BalanceWhiteAuto", "Off", False)],
    "TriggerSource": [("TriggerMode", "Off", True)],
    "TriggerActivation": [("TriggerMode", "Off", True)],
    "TriggerOverlap": [("TriggerMode", "Off", True)],
    "TriggerSelector": [("TriggerMode", "Off", True)],
}

GRAB_TIMEOUT_MS = 100


class AcquisitionThread(QThread):
    """Grabs frames as fast as the camera delivers them and keeps ONLY the
    newest one. The GUI is signalled once per frame it has consumed, so a
    slow display never builds up a backlog (the cause of SpinView's lag)."""

    frame_ready = Signal()
    failed = Signal(str)

    def __init__(self, backend: CameraBackend) -> None:
        super().__init__()
        self._backend = backend
        self._running = False
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._pending = False
        self.frames_received = 0
        self.frames_displayed = 0

    def run(self) -> None:
        self._running = True
        while self._running:
            try:
                frame = self._backend.grab(GRAB_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001 - report anything to the GUI
                if self._running:
                    self.failed.emit(str(exc))
                break
            if frame is None:
                continue
            with self._lock:
                self._latest = frame
                self.frames_received += 1
                notify = not self._pending
                self._pending = True
            if notify:
                self.frame_ready.emit()

    def request_stop(self) -> None:
        self._running = False

    def take_latest(self) -> Frame | None:
        with self._lock:
            frame = self._latest
            self._pending = False
            if frame is not None:
                self.frames_displayed += 1
            return frame


class CameraController(QObject):
    frame_ready = Signal(object)  # Frame (newest only)
    opened = Signal(object)  # DeviceInfo
    closed = Signal()
    streaming_changed = Signal(bool)
    features_changed = Signal()
    message = Signal(str)  # status-bar text (errors and notes)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend: CameraBackend | None = None
        self.device: DeviceInfo | None = None
        self._thread: AcquisitionThread | None = None
        self._fps_t = time.perf_counter()
        self._fps_counts = (0, 0)
        self.last_frame: Frame | None = None

    # ------------------------------------------------------------ lifetime
    @property
    def is_open(self) -> bool:
        return self.backend is not None and self.backend.is_open

    @property
    def is_streaming(self) -> bool:
        return self._thread is not None

    def open(self, backend: CameraBackend, serial: str | None = None) -> DeviceInfo:
        self.close()
        self.device = backend.open(serial)
        self.backend = backend
        self.opened.emit(self.device)
        self.features_changed.emit()
        return self.device

    def close(self) -> None:
        if self.backend is None:
            return
        self.stop()
        try:
            self.backend.close()
        finally:
            self.backend = None
            self.device = None
            self.last_frame = None
            self.closed.emit()

    def start(self) -> None:
        if not self.is_open or self.is_streaming:
            return
        assert self.backend is not None
        try:
            self.backend.start()
        except CameraError as exc:
            self.message.emit(f"Could not start acquisition: {exc}")
            return
        self._thread = AcquisitionThread(self.backend)
        self._thread.frame_ready.connect(self._on_frame_ready)
        self._thread.failed.connect(self._on_thread_failed)
        self._fps_t = time.perf_counter()
        self._fps_counts = (0, 0)
        self._thread.start()
        self.streaming_changed.emit(True)
        self.features_changed.emit()

    def stop(self) -> None:
        if self._thread is None:
            return
        thread, self._thread = self._thread, None
        thread.request_stop()
        thread.wait(5000)
        thread.frame_ready.disconnect(self._on_frame_ready)
        assert self.backend is not None
        try:
            self.backend.stop()
        except CameraError as exc:
            self.message.emit(f"Error stopping acquisition: {exc}")
        self.streaming_changed.emit(False)
        self.features_changed.emit()

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Temporarily stop streaming (e.g. to change ROI) and resume after."""
        was_streaming = self.is_streaming
        if was_streaming:
            self.stop()
        try:
            yield
        finally:
            if was_streaming:
                self.start()

    def snap(self) -> None:
        """Acquire a single frame (or fire a software trigger if live in
        software-trigger mode)."""
        if not self.is_open:
            return
        software = self.get("TriggerMode") == "On" and self.get("TriggerSource") == "Software"
        if self.is_streaming:
            if software:
                self.execute("TriggerSoftware")
            return
        self._single_shot = True
        self.start()
        if software:
            self.execute("TriggerSoftware")

    _single_shot = False

    def _on_frame_ready(self) -> None:
        if self._thread is None:
            return
        frame = self._thread.take_latest()
        if frame is not None:
            self.last_frame = frame
            self.frame_ready.emit(frame)
            if self._single_shot:
                self._single_shot = False
                QTimer.singleShot(0, self.stop)

    def _on_thread_failed(self, text: str) -> None:
        self.message.emit(f"Acquisition error: {text}")
        self.stop()

    def measured_rates(self) -> tuple[float, float]:
        """(camera fps, display fps) since the previous call."""
        if self._thread is None:
            return 0.0, 0.0
        now = time.perf_counter()
        dt = max(now - self._fps_t, 1e-6)
        rx, disp = self._thread.frames_received, self._thread.frames_displayed
        fps = ((rx - self._fps_counts[0]) / dt, (disp - self._fps_counts[1]) / dt)
        self._fps_t, self._fps_counts = now, (rx, disp)
        return fps

    # ------------------------------------------------------------ features
    def has(self, name: str) -> bool:
        return self.is_open and self.backend.has_feature(name)  # type: ignore[union-attr]

    def get(self, name: str, default: Any = None) -> Any:
        if not self.has(name):
            return default
        try:
            return self.backend.get_value(name)  # type: ignore[union-attr]
        except CameraError:
            return default

    def describe(self, name: str) -> FeatureInfo | None:
        """FeatureInfo with ``settable``/``note`` explaining how the GUI will
        handle a feature that the camera currently reports as locked."""
        if not self.has(name):
            return None
        assert self.backend is not None
        try:
            info = self.backend.get_info(name)
        except CameraError:
            return None
        if info is None:
            return None
        if info.writable:
            info.settable = True
            return info
        if info.note == "Read-only" or info.type == "string":
            info.settable = False
            info.note = info.note or "Read-only"
            return info
        blockers = self._unsatisfied_prerequisites(name)
        if info.type == "command" and name.startswith("Trigger"):
            # Pausing the stream never enables a trigger command; it depends
            # on the trigger configuration, which the camera's note explains.
            info.settable = False
            info.note = info.note or "Requires TriggerMode = On and TriggerSource = Software"
        elif blockers:
            info.settable = True
            info.note = "Will set " + ", ".join(f"{f} = {v}" for f, v, _ in blockers) + " automatically"
        elif self.is_streaming:
            info.settable = True
            info.note = "Acquisition will pause briefly to apply"
        else:
            info.settable = False
            info.note = info.note or "Not writable in the current camera state"
        return info

    def _unsatisfied_prerequisites(self, name: str) -> list[tuple[str, Any, bool]]:
        out = []
        for feat, required, restore in PREREQUISITES.get(name, []):
            if self.has(feat) and self.get(feat) != required:
                out.append((feat, required, restore))
        return out

    def set(self, name: str, value: Any) -> bool:
        """Set a feature, resolving prerequisites and pausing the stream if
        required. Returns True on success; errors go to :attr:`message`."""
        if not self.is_open:
            return False
        assert self.backend is not None
        try:
            self._set_resolving(name, value)
            return True
        except CameraError as exc:
            self.message.emit(f"Could not set {name} = {value}: {exc}")
            return False
        finally:
            self.features_changed.emit()

    def _set_resolving(self, name: str, value: Any) -> None:
        assert self.backend is not None
        restore: list[tuple[str, Any]] = []
        for feat, required, do_restore in self._unsatisfied_prerequisites(name):
            if do_restore:
                restore.append((feat, self.get(feat)))
            self._set_maybe_paused(feat, required)
        try:
            self._set_maybe_paused(name, value)
        finally:
            for feat, old in reversed(restore):
                self._set_maybe_paused(feat, old)

    def _set_maybe_paused(self, name: str, value: Any) -> None:
        assert self.backend is not None
        info = self.backend.get_info(name)
        if info is not None and not info.writable and self.is_streaming:
            with self.paused():
                self.backend.set_value(name, value)
        else:
            self.backend.set_value(name, value)

    def execute(self, name: str) -> bool:
        if not self.is_open:
            return False
        assert self.backend is not None
        try:
            info = self.backend.get_info(name)
            if info is not None and not info.writable and self.is_streaming \
                    and not name.startswith("Trigger"):
                with self.paused():
                    self.backend.execute(name)
            else:
                self.backend.execute(name)
            return True
        except CameraError as exc:
            self.message.emit(f"Could not execute {name}: {exc}")
            return False
        finally:
            self.features_changed.emit()

    # ---------------------------------------------------------------- ROI
    def set_roi(self, x: int, y: int, w: int, h: int) -> bool:
        """Apply an ROI in one go, rounding to the camera's increments and
        ordering the writes so the offsets never push Width/Height out of
        range."""
        if not self.is_open:
            return False
        assert self.backend is not None
        try:
            with self.paused():
                b = self.backend
                wmax = int(self.get("WidthMax") or self.get("SensorWidth"))
                hmax = int(self.get("HeightMax") or self.get("SensorHeight"))
                b.set_value("OffsetX", 0)
                b.set_value("OffsetY", 0)
                w = _snap(w, b.get_info("Width"), wmax)
                h = _snap(h, b.get_info("Height"), hmax)
                b.set_value("Width", w)
                b.set_value("Height", h)
                x = _snap(min(x, wmax - w), b.get_info("OffsetX"), wmax - w)
                y = _snap(min(y, hmax - h), b.get_info("OffsetY"), hmax - h)
                b.set_value("OffsetX", x)
                b.set_value("OffsetY", y)
            self.message.emit(f"ROI set to {w}x{h} at ({x}, {y})")
            return True
        except CameraError as exc:
            self.message.emit(f"Could not set ROI: {exc}")
            return False
        finally:
            self.features_changed.emit()

    def full_roi(self) -> bool:
        wmax = int(self.get("WidthMax") or self.get("SensorWidth") or 0)
        hmax = int(self.get("HeightMax") or self.get("SensorHeight") or 0)
        return self.set_roi(0, 0, wmax, hmax)


def _snap(value: int, info: FeatureInfo | None, upper: int) -> int:
    """Round ``value`` down to a valid multiple of the feature increment,
    clamped to [min, upper]."""
    lo = int(info.min or 0) if info else 0
    inc = int(info.inc or 1) if info else 1
    value = max(lo, min(int(value), upper))
    return lo + ((value - lo) // inc) * inc
