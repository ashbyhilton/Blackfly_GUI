"""Real-hardware backend using Teledyne FLIR's PySpin (Spinnaker SDK).

Works with any Spinnaker (GenICam / USB3 Vision / GigE Vision) camera: all
features are accessed generically by name through the node map, so the GUI
adapts to whatever the connected camera supports.

Low latency
-----------
The stream is configured with ``StreamBufferHandlingMode = NewestOnly`` and
a small manual buffer count, so ``GetNextImage`` always returns the most
recent frame instead of working through a queue of stale ones.
"""

from __future__ import annotations

import atexit
import threading
from typing import Any

import numpy as np
import PySpin

from .base import CameraBackend, CameraError, DeviceInfo, FeatureInfo, Frame
from .packed import unpack_mono10p, unpack_mono12p

STREAM_BUFFER_COUNT = 3
TIMEOUT_ERROR = -1011  # SPINNAKER_ERR_TIMEOUT

_MAX_VALUE = {
    "Mono8": 255, "Mono10": 1023, "Mono10p": 1023, "Mono10Packed": 1023,
    "Mono12": 4095, "Mono12p": 4095, "Mono12Packed": 4095, "Mono14": 16383, "Mono16": 65535,
}

# ----------------------------------------------------------------- system
_system = None
_system_lock = threading.Lock()


def _get_system():
    global _system
    with _system_lock:
        if _system is None:
            _system = PySpin.System.GetInstance()
            atexit.register(_release_system)
        return _system


def _release_system() -> None:
    global _system
    with _system_lock:
        if _system is not None:
            try:
                _system.ReleaseInstance()
            except PySpin.SpinnakerException:
                pass  # cameras still referenced; process is exiting anyway
            _system = None


def _err(exc: Exception) -> CameraError:
    return CameraError(str(exc).strip())


class SpinnakerCamera(CameraBackend):
    def __init__(self) -> None:
        self._cam = None
        self._nodemap = None
        self._stream_nodemap = None
        self._tl_nodemap = None
        self._streaming = False
        self._processor = None
        self._incomplete = 0
        self._device: DeviceInfo | None = None

    # ------------------------------------------------------- discovery
    @staticmethod
    def enumerate() -> list[DeviceInfo]:
        system = _get_system()
        cams = system.GetCameras()
        out = []
        try:
            for i in range(cams.GetSize()):
                cam = cams.GetByIndex(i)
                try:
                    out.append(_device_info(cam))
                finally:
                    del cam
        finally:
            cams.Clear()
        return out

    def list_devices(self) -> list[DeviceInfo]:
        return self.enumerate()

    # -------------------------------------------------------- lifetime
    def open(self, serial: str | None = None) -> DeviceInfo:
        system = _get_system()
        cams = system.GetCameras()
        try:
            if cams.GetSize() == 0:
                raise CameraError("No Spinnaker cameras found")
            cam = cams.GetBySerial(serial) if serial else cams.GetByIndex(0)
            if cam is None or not cam.IsValid():
                raise CameraError(f"Camera {serial} not found")
            self._device = _device_info(cam)
            try:
                cam.Init()
            except PySpin.SpinnakerException as exc:
                del cam
                raise CameraError(f"Could not initialise camera (in use by another program?): {exc}") from exc
            self._cam = cam
        finally:
            cams.Clear()
        self._nodemap = self._cam.GetNodeMap()
        self._stream_nodemap = self._cam.GetTLStreamNodeMap()
        self._tl_nodemap = self._cam.GetTLDeviceNodeMap()
        self._processor = PySpin.ImageProcessor()
        self._processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
        self._configure_stream()
        # The GUI streams continuously; a camera left in SingleFrame mode by
        # another program would otherwise stop after one image.
        self._try_set("AcquisitionMode", "Continuous")
        return self._device

    def _configure_stream(self) -> None:
        self._try_set("StreamBufferHandlingMode", "NewestOnly")
        self._try_set("StreamBufferCountMode", "Manual")
        info = self.get_info("StreamBufferCountManual")
        if info is not None and info.writable:
            count = int(max(info.min or 1, min(STREAM_BUFFER_COUNT, info.max or STREAM_BUFFER_COUNT)))
            self._try_set("StreamBufferCountManual", count)

    def _try_set(self, name: str, value: Any) -> None:
        try:
            info = self.get_info(name)
            if info is not None and info.writable:
                self.set_value(name, value)
        except CameraError:
            pass

    def close(self) -> None:
        if self._cam is None:
            return
        self.stop()
        try:
            self._cam.DeInit()
        except PySpin.SpinnakerException:
            pass
        self._nodemap = self._stream_nodemap = self._tl_nodemap = None
        self._processor = None
        del self._cam
        self._cam = None

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    # ------------------------------------------------------ acquisition
    def start(self) -> None:
        if self._cam is None:
            raise CameraError("Camera is not open")
        if self._streaming:
            return
        try:
            self._cam.BeginAcquisition()
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc
        self._streaming = True

    def stop(self) -> None:
        if self._cam is None or not self._streaming:
            return
        self._streaming = False
        try:
            self._cam.EndAcquisition()
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    def grab(self, timeout_ms: int) -> Frame | None:
        cam = self._cam
        if cam is None or not self._streaming:
            return None
        try:
            image = cam.GetNextImage(timeout_ms)
        except PySpin.SpinnakerException as exc:
            if getattr(exc, "errorcode", None) == TIMEOUT_ERROR or "timeout" in str(exc).lower():
                return None
            if not self._streaming:
                return None
            raise _err(exc) from exc
        try:
            if image.IsIncomplete():
                self._incomplete += 1
                return None
            return self._to_frame(image)
        finally:
            image.Release()

    def _to_frame(self, image) -> Frame:
        fmt = image.GetPixelFormatName()
        w, h = image.GetWidth(), image.GetHeight()
        if fmt in ("Mono8", "Mono16"):
            arr = np.array(image.GetNDArray(), copy=True)
            max_value = _MAX_VALUE[fmt]
        elif fmt == "Mono12p":
            arr = unpack_mono12p(image.GetData(), w, h)
            max_value = 4095
        elif fmt == "Mono10p":
            arr = unpack_mono10p(image.GetData(), w, h)
            max_value = 1023
        elif fmt.startswith("Mono"):
            target = PySpin.PixelFormat_Mono8 if fmt == "Mono8" else PySpin.PixelFormat_Mono16
            conv = self._processor.Convert(image, target)
            arr = np.array(conv.GetNDArray(), copy=True)
            max_value = 255 if fmt == "Mono8" else 65535
        else:
            # Bayer / RGB / YUV: demosaic or convert to RGB for display.
            high = any(s in fmt for s in ("10", "12", "16"))
            target = PySpin.PixelFormat_RGB16 if high else PySpin.PixelFormat_RGB8
            conv = self._processor.Convert(image, target)
            arr = np.array(conv.GetNDArray(), copy=True).reshape(h, w, 3)
            max_value = 65535 if high else 255
        return Frame(arr, int(image.GetFrameID()), image.GetTimeStamp() * 1e-9, fmt, max_value)

    def stream_stats(self) -> dict[str, float]:
        stats: dict[str, float] = {"incomplete": float(self._incomplete)}
        for name, key in (("StreamDroppedFrameCount", "dropped"), ("StreamLostFrameCount", "lost")):
            v = self.get_value_or_none(name)
            if v is not None:
                stats[key] = float(v)
        return stats

    # ---------------------------------------------------------- features
    def _node(self, name: str):
        for nm in (self._nodemap, self._stream_nodemap, self._tl_nodemap):
            if nm is None:
                continue
            node = nm.GetNode(name)
            if node is not None and PySpin.IsAvailable(node):
                return node
        return None

    def has_feature(self, name: str) -> bool:
        return self._node(name) is not None

    def get_value_or_none(self, name: str) -> Any:
        try:
            return self.get_value(name)
        except CameraError:
            return None

    def get_value(self, name: str) -> Any:
        node = self._node(name)
        if node is None:
            raise CameraError(f"Camera has no feature {name}")
        if not PySpin.IsReadable(node):
            raise CameraError(f"{name} is not readable")
        try:
            return _read(node)
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc

    def get_info(self, name: str) -> FeatureInfo | None:
        node = self._node(name)
        if node is None:
            return None
        try:
            return _describe(node)
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc

    def set_value(self, name: str, value: Any) -> None:
        node = self._node(name)
        if node is None:
            raise CameraError(f"Camera has no feature {name}")
        if not PySpin.IsWritable(node):
            raise CameraError(f"{name} is not writable in the current state")
        t = node.GetPrincipalInterfaceType()
        try:
            if t == PySpin.intfIFloat:
                PySpin.CFloatPtr(node).SetValue(float(value))
            elif t == PySpin.intfIInteger:
                PySpin.CIntegerPtr(node).SetValue(int(value))
            elif t == PySpin.intfIBoolean:
                PySpin.CBooleanPtr(node).SetValue(bool(value))
            elif t == PySpin.intfIEnumeration:
                enum = PySpin.CEnumerationPtr(node)
                entry = enum.GetEntryByName(str(value))
                if entry is None or not PySpin.IsReadable(entry):
                    raise CameraError(f"{value!r} is not an available entry of {name}")
                enum.SetIntValue(entry.GetValue())
            elif t == PySpin.intfIString:
                PySpin.CStringPtr(node).SetValue(str(value))
            else:
                raise CameraError(f"Cannot set {name} (unsupported type)")
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc

    def execute(self, name: str) -> None:
        node = self._node(name)
        if node is None or node.GetPrincipalInterfaceType() != PySpin.intfICommand:
            raise CameraError(f"{name} is not a command")
        if not PySpin.IsWritable(node):
            raise CameraError(f"{name} is not executable in the current state")
        try:
            PySpin.CCommandPtr(node).Execute()
        except PySpin.SpinnakerException as exc:
            raise _err(exc) from exc

    def feature_tree(self) -> list[tuple[str, list[str]]]:
        out: list[tuple[str, list[str]]] = []
        if self._nodemap is not None:
            root = self._nodemap.GetNode("Root")
            for cat in PySpin.CCategoryPtr(root).GetFeatures():
                if cat.GetPrincipalInterfaceType() != PySpin.intfICategory or not _visible(cat):
                    continue
                names: list[str] = []
                _collect(cat, names)
                if names:
                    out.append((cat.GetDisplayName(), names))
        if self._stream_nodemap is not None:
            names = []
            for node in self._stream_nodemap.GetNodes():
                if node.GetPrincipalInterfaceType() not in (PySpin.intfICategory,) \
                        and _visible(node) and PySpin.IsAvailable(node):
                    names.append(node.GetName())
            if names:
                out.append(("Host Stream (buffers)", sorted(names)))
        return out


# ------------------------------------------------------------- helpers
def _device_info(cam) -> DeviceInfo:
    tl = cam.GetTLDeviceNodeMap()

    def s(name: str) -> str:
        node = PySpin.CStringPtr(tl.GetNode(name))
        return node.GetValue() if node is not None and PySpin.IsReadable(node) else ""

    return DeviceInfo(serial=s("DeviceSerialNumber"), model=s("DeviceModelName"),
                      vendor=s("DeviceVendorName"), interface=s("DeviceType") or "Spinnaker")


def _visible(node) -> bool:
    invisible = getattr(PySpin, "Invisible", None)
    try:
        return invisible is None or node.GetVisibility() != invisible
    except PySpin.SpinnakerException:
        return True


def _collect(cat, names: list[str]) -> None:
    for node in PySpin.CCategoryPtr(cat).GetFeatures():
        if not _visible(node) or not PySpin.IsAvailable(node):
            continue
        if node.GetPrincipalInterfaceType() == PySpin.intfICategory:
            _collect(node, names)
        else:
            names.append(node.GetName())


_TYPE_NAMES = None


def _type_name(node) -> str:
    global _TYPE_NAMES
    if _TYPE_NAMES is None:
        _TYPE_NAMES = {
            PySpin.intfIFloat: "float", PySpin.intfIInteger: "int",
            PySpin.intfIEnumeration: "enum", PySpin.intfIBoolean: "bool",
            PySpin.intfICommand: "command", PySpin.intfIString: "string",
        }
    return _TYPE_NAMES.get(node.GetPrincipalInterfaceType(), "other")


def _read(node) -> Any:
    t = node.GetPrincipalInterfaceType()
    if t == PySpin.intfIFloat:
        return PySpin.CFloatPtr(node).GetValue()
    if t == PySpin.intfIInteger:
        return PySpin.CIntegerPtr(node).GetValue()
    if t == PySpin.intfIBoolean:
        return PySpin.CBooleanPtr(node).GetValue()
    if t == PySpin.intfIEnumeration:
        return PySpin.CEnumerationPtr(node).GetCurrentEntry().GetSymbolic()
    if t == PySpin.intfIString:
        return PySpin.CStringPtr(node).GetValue()
    return None


def _describe(node) -> FeatureInfo:
    kind = _type_name(node)
    readable = PySpin.IsReadable(node)
    writable = PySpin.IsWritable(node)
    info = FeatureInfo(
        name=node.GetName(), type=kind, display_name=node.GetDisplayName(),
        description=node.GetDescription() or node.GetToolTip() or "",
        readable=readable, writable=writable,
    )
    if not readable:
        return info
    if kind != "command":
        info.value = _read(node)
    if kind == "float":
        f = PySpin.CFloatPtr(node)
        info.min, info.max, info.unit = f.GetMin(), f.GetMax(), f.GetUnit()
        if f.HasInc():
            info.inc = f.GetInc()
    elif kind == "int":
        i = PySpin.CIntegerPtr(node)
        info.min, info.max, info.inc = i.GetMin(), i.GetMax(), i.GetInc()
    elif kind == "enum":
        e = PySpin.CEnumerationPtr(node)
        info.entries = [PySpin.CEnumEntryPtr(x).GetSymbolic() for x in e.GetEntries()
                        if PySpin.IsReadable(x)]
    # Read-only by design vs. temporarily locked. The *current* access mode is
    # RO for e.g. Width while streaming, so use the static (imposed) mode from
    # the camera's XML description to identify genuinely read-only features.
    if not writable:
        try:
            if node.GetImposedAccessMode() == PySpin.RO:
                info.note = "Read-only"
        except (AttributeError, PySpin.SpinnakerException):
            pass
    return info
