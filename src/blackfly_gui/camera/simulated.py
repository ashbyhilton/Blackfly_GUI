"""A simulated Spinnaker-like camera.

Models a mono Sony IMX265 Blackfly S (BFS-U3-31S4M): 2048x1536, 3.45 um
pixels, 10/12-bit ADC. It reproduces the GenICam *access rules* that make
SpinView feel "locked" (ROI/binning/pixel format only writable while
stopped, ExposureTime only writable with ExposureAuto=Off, TriggerSource only
writable with TriggerMode=Off, frame-rate limits that depend on exposure and
ROI ...) so the GUI's dependency handling can be developed and tested
without hardware. The image is a noisy, slowly orbiting, elliptical
Gaussian spot -- handy for trying the Gaussian fit.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .base import CameraBackend, CameraError, DeviceInfo, FeatureInfo, Frame

SENSOR_W, SENSOR_H = 2048, 1536
FULL_WELL_E = 10500.0
READ_NOISE_E = 2.5
FULL_FRAME_FPS = 55.7
USB_BYTES_PER_S = 380e6


@dataclass
class _Node:
    name: str
    type: str
    value: Any = None
    category: str = ""
    min: float | None = None
    max: float | None = None
    inc: float | None = None
    entries: list[str] = field(default_factory=list)
    unit: str = ""
    description: str = ""
    readonly: bool = False
    # returns None if writable, else a reason string
    lock: Callable[[], str | None] | None = None
    # dynamic range overrides
    min_fn: Callable[[], float] | None = None
    max_fn: Callable[[], float] | None = None
    getter: Callable[[], Any] | None = None
    on_set: Callable[[Any], None] | None = None
    command: Callable[[], None] | None = None


class SimulatedCamera(CameraBackend):
    SERIAL = "SIM00001"
    MODEL = "Simulated Blackfly S BFS-U3-31S4M"

    def __init__(self) -> None:
        self._open = False
        self._streaming = False
        self._lock = threading.RLock()
        self._trigger_event = threading.Event()
        self._rng = np.random.default_rng()
        self._frame_id = 0
        self._next_frame_t = 0.0
        self._t0 = time.perf_counter()
        self._user_sets: dict[str, dict[str, Any]] = {}
        self._nodes: dict[str, _Node] = {}
        self._build_nodes()
        self._user_sets["Default"] = self._snapshot_settings()

    # ------------------------------------------------------------------ nodes
    def _build_nodes(self) -> None:
        streaming_lock = lambda: "Locked while streaming" if self._streaming else None  # noqa: E731

        def add(node: _Node) -> None:
            self._nodes[node.name] = node

        v = self._v

        # Device control
        cat = "Device Control"
        add(_Node("DeviceVendorName", "string", "FLIR (simulated)", cat, readonly=True))
        add(_Node("DeviceModelName", "string", self.MODEL, cat, readonly=True))
        add(_Node("DeviceSerialNumber", "string", self.SERIAL, cat, readonly=True))
        add(_Node("DeviceTemperature", "float", 0.0, cat, unit="C", readonly=True,
                  getter=lambda: 42.0 + 1.5 * math.sin((time.perf_counter() - self._t0) / 60),
                  description="Device temperature in degrees Celsius."))

        # Acquisition control
        cat = "Acquisition Control"
        add(_Node("AcquisitionMode", "enum", "Continuous", cat,
                  entries=["Continuous", "SingleFrame", "MultiFrame"], lock=streaming_lock,
                  description="Sets the acquisition mode of the device."))
        add(_Node("ExposureAuto", "enum", "Off", cat, entries=["Off", "Once", "Continuous"],
                  description="Sets the automatic exposure mode."))
        add(_Node("ExposureTime", "float", 10000.0, cat, unit="us",
                  min=4.0, max_fn=self._exposure_max, inc=None,
                  lock=lambda: None if v("ExposureAuto") == "Off" else "Requires ExposureAuto = Off",
                  description="Exposure time in microseconds."))
        add(_Node("AcquisitionFrameRateEnable", "bool", False, cat,
                  description="If enabled, AcquisitionFrameRate limits the frame rate."))
        add(_Node("AcquisitionFrameRate", "float", 30.0, cat, unit="Hz", min=1.0,
                  max_fn=self._max_frame_rate,
                  lock=lambda: None if v("AcquisitionFrameRateEnable")
                  else "Requires AcquisitionFrameRateEnable = True",
                  description="User controlled acquisition frame rate in Hertz."))
        add(_Node("AcquisitionResultingFrameRate", "float", 0.0, cat, unit="Hz", readonly=True,
                  getter=self._resulting_frame_rate,
                  description="Resulting frame rate given all current settings."))
        add(_Node("TriggerSelector", "enum", "FrameStart", cat, entries=["FrameStart"]))
        add(_Node("TriggerMode", "enum", "Off", cat, entries=["Off", "On"],
                  description="Controls whether the selected trigger is active."))
        trig_lock = lambda: "Requires TriggerMode = Off" if v("TriggerMode") == "On" else None  # noqa: E731
        add(_Node("TriggerSource", "enum", "Software", cat,
                  entries=["Software", "Line0", "Line2", "Line3"], lock=trig_lock,
                  description="Specifies the internal signal or physical input line to use as the trigger source."))
        add(_Node("TriggerActivation", "enum", "RisingEdge", cat,
                  entries=["RisingEdge", "FallingEdge"], lock=trig_lock,
                  description="Specifies the activation mode of the trigger."))
        add(_Node("TriggerDelay", "float", 9.0, cat, unit="us", min=9.0, max=65520.0,
                  description="Delay in microseconds between trigger reception and activation."))
        add(_Node("TriggerOverlap", "enum", "Off", cat, entries=["Off", "ReadOut"],
                  lock=trig_lock, description="Specifies the overlap mode of the trigger."))
        add(_Node("TriggerSoftware", "command", None, cat, command=self._fire_trigger,
                  lock=lambda: None if (v("TriggerMode") == "On" and v("TriggerSource") == "Software")
                  else "Requires TriggerMode = On and TriggerSource = Software",
                  description="Generates an internal trigger."))

        # Analog control
        cat = "Analog Control"
        add(_Node("GainAuto", "enum", "Off", cat, entries=["Off", "Once", "Continuous"],
                  description="Sets the automatic gain mode."))
        add(_Node("Gain", "float", 0.0, cat, unit="dB", min=0.0, max=47.994,
                  lock=lambda: None if v("GainAuto") == "Off" else "Requires GainAuto = Off",
                  description="Controls the amplification of the video signal in dB."))
        add(_Node("BlackLevel", "float", 1.0, cat, unit="%", min=0.0, max=10.0,
                  description="Controls the offset of the video signal in percent."))
        add(_Node("GammaEnable", "bool", False, cat, description="Enables/disables gamma correction."))
        add(_Node("Gamma", "float", 0.8, cat, min=0.25, max=4.0,
                  lock=lambda: None if v("GammaEnable") else "Requires GammaEnable = True",
                  description="Controls the gamma correction of pixel intensity."))

        # Image format control
        cat = "Image Format Control"
        add(_Node("SensorWidth", "int", SENSOR_W, cat, readonly=True))
        add(_Node("SensorHeight", "int", SENSOR_H, cat, readonly=True))
        add(_Node("WidthMax", "int", 0, cat, readonly=True, getter=lambda: self._width_max()))
        add(_Node("HeightMax", "int", 0, cat, readonly=True, getter=lambda: self._height_max()))
        add(_Node("Width", "int", SENSOR_W, cat, min=32, inc=16, lock=streaming_lock,
                  max_fn=lambda: self._width_max() - v("OffsetX"),
                  description="Width of the image provided by the device (in pixels)."))
        add(_Node("Height", "int", SENSOR_H, cat, min=8, inc=2, lock=streaming_lock,
                  max_fn=lambda: self._height_max() - v("OffsetY"),
                  description="Height of the image provided by the device (in pixels)."))
        add(_Node("OffsetX", "int", 0, cat, min=0, inc=4, lock=streaming_lock,
                  max_fn=lambda: self._width_max() - v("Width"),
                  description="Horizontal offset from the origin to the ROI (in pixels)."))
        add(_Node("OffsetY", "int", 0, cat, min=0, inc=2, lock=streaming_lock,
                  max_fn=lambda: self._height_max() - v("Height"),
                  description="Vertical offset from the origin to the ROI (in pixels)."))
        add(_Node("PixelFormat", "enum", "Mono8", cat, entries=["Mono8", "Mono12p", "Mono16"],
                  lock=streaming_lock, description="Format of the pixels provided by the device."))
        add(_Node("AdcBitDepth", "enum", "Bit10", cat, entries=["Bit10", "Bit12"],
                  lock=streaming_lock, description="Selects which ADC bit depth to use."))
        add(_Node("BinningSelector", "enum", "All", cat, entries=["All", "Sensor", "ISP"]))
        for axis in ("Horizontal", "Vertical"):
            add(_Node(f"Binning{axis}Mode", "enum", "Sum", cat, entries=["Sum", "Average"],
                      lock=streaming_lock, description=f"Selects how to combine {axis.lower()}ly binned pixels."))
            add(_Node(f"Binning{axis}", "int", 1, cat, min=1, max=4, inc=1, lock=streaming_lock,
                      on_set=lambda _val: self._reset_roi(),
                      description=f"Number of {axis.lower()} pixels to combine together."))
            add(_Node(f"Decimation{axis}", "int", 1, cat, min=1, max=4, inc=1, lock=streaming_lock,
                      on_set=lambda _val: self._reset_roi(),
                      description=f"{axis} sub-sampling of the image."))
        add(_Node("ReverseX", "bool", False, cat, lock=streaming_lock,
                  description="Flip the image horizontally."))
        add(_Node("ReverseY", "bool", False, cat, lock=streaming_lock,
                  description="Flip the image vertically."))

        # User sets
        cat = "User Set Control"
        add(_Node("UserSetSelector", "enum", "Default", cat,
                  entries=["Default", "UserSet0", "UserSet1"],
                  description="Selects the user set to load, save or configure."))
        add(_Node("UserSetLoad", "command", None, cat, command=self._user_set_load,
                  lock=streaming_lock, description="Loads the selected user set."))
        add(_Node("UserSetSave", "command", None, cat, command=self._user_set_save,
                  lock=lambda: "The Default user set is read-only"
                  if v("UserSetSelector") == "Default" else None,
                  description="Saves the current settings to the selected user set."))
        add(_Node("UserSetDefault", "enum", "Default", cat,
                  entries=["Default", "UserSet0", "UserSet1"],
                  description="Selects the user set loaded at power-up."))

    def _v(self, name: str) -> Any:
        node = self._nodes[name]
        return node.getter() if node.getter else node.value

    # ----------------------------------------------------- derived quantities
    def _width_max(self) -> int:
        return SENSOR_W // (self._v("BinningHorizontal") * self._v("DecimationHorizontal"))

    def _height_max(self) -> int:
        return SENSOR_H // (self._v("BinningVertical") * self._v("DecimationVertical"))

    def _bytes_per_pixel(self) -> float:
        return {"Mono8": 1.0, "Mono12p": 1.5, "Mono16": 2.0}[self._v("PixelFormat")]

    def _sensor_limit_fps(self) -> float:
        rows = self._v("Height") * self._v("DecimationVertical")
        readout = FULL_FRAME_FPS * SENSOR_H / max(rows, 64)
        bw = USB_BYTES_PER_S / (self._v("Width") * self._v("Height") * self._bytes_per_pixel())
        return min(readout, bw, 1000.0)

    def _max_frame_rate(self) -> float:
        exp_limit = 1e6 / (self._v("ExposureTime") + 20.0)
        return max(1.0, min(self._sensor_limit_fps(), exp_limit))

    def _exposure_max(self) -> float:
        if self._v("AcquisitionFrameRateEnable"):
            return max(4.0, 1e6 / self._v("AcquisitionFrameRate") - 20.0)
        return 30_000_000.0

    def _resulting_frame_rate(self) -> float:
        fps = self._max_frame_rate()
        if self._v("AcquisitionFrameRateEnable"):
            fps = min(fps, self._v("AcquisitionFrameRate"))
        return fps

    def _reset_roi(self) -> None:
        self._nodes["OffsetX"].value = 0
        self._nodes["OffsetY"].value = 0
        self._nodes["Width"].value = self._width_max()
        self._nodes["Height"].value = self._height_max()

    # ----------------------------------------------------------- user sets
    _USER_SET_EXCLUDE = {"UserSetSelector", "UserSetDefault"}

    def _snapshot_settings(self) -> dict[str, Any]:
        return {n.name: n.value for n in self._nodes.values()
                if not n.readonly and n.type != "command" and n.name not in self._USER_SET_EXCLUDE}

    def _user_set_save(self) -> None:
        self._user_sets[self._v("UserSetSelector")] = self._snapshot_settings()

    def _user_set_load(self) -> None:
        settings = self._user_sets.get(self._v("UserSetSelector"))
        if settings is None:
            raise CameraError(f"{self._v('UserSetSelector')} has never been saved")
        for name, value in settings.items():
            self._nodes[name].value = value

    def _fire_trigger(self) -> None:
        self._trigger_event.set()

    # ------------------------------------------------------- CameraBackend
    def list_devices(self) -> list[DeviceInfo]:
        return [DeviceInfo(self.SERIAL, self.MODEL, "FLIR (simulated)", "Simulated")]

    def open(self, serial: str | None = None) -> DeviceInfo:
        if serial not in (None, self.SERIAL):
            raise CameraError(f"No simulated camera with serial {serial}")
        self._open = True
        return self.list_devices()[0]

    def close(self) -> None:
        self.stop()
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def start(self) -> None:
        self._require_open()
        with self._lock:
            self._streaming = True
            self._trigger_event.clear()
            self._next_frame_t = time.perf_counter()

    def stop(self) -> None:
        with self._lock:
            self._streaming = False
        self._trigger_event.set()  # wake a waiting grab()

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    def has_feature(self, name: str) -> bool:
        return name in self._nodes

    def get_info(self, name: str) -> FeatureInfo | None:
        node = self._nodes.get(name)
        if node is None:
            return None
        with self._lock:
            reason = None if node.readonly else (node.lock() if node.lock else None)
            writable = not node.readonly and reason is None
            info = FeatureInfo(
                name=node.name, type=node.type, value=self._v(name),
                min=node.min_fn() if node.min_fn else node.min,
                max=node.max_fn() if node.max_fn else node.max,
                inc=node.inc, entries=list(node.entries), unit=node.unit,
                description=node.description, readable=True, writable=writable,
            )
            if node.readonly:
                info.note = "Read-only"
            elif reason:
                info.note = reason
        return info

    def get_value(self, name: str) -> Any:
        if name not in self._nodes:
            raise CameraError(f"Unknown feature {name}")
        with self._lock:
            return self._v(name)

    def set_value(self, name: str, value: Any) -> None:
        self._require_open()
        node = self._nodes.get(name)
        if node is None:
            raise CameraError(f"Unknown feature {name}")
        with self._lock:
            if node.readonly:
                raise CameraError(f"{name} is read-only")
            if node.lock and (reason := node.lock()):
                raise CameraError(f"{name} is not writable: {reason}")
            if node.type == "enum":
                if value not in node.entries:
                    raise CameraError(f"{value!r} is not a valid entry for {name}")
            elif node.type in ("int", "float"):
                lo = node.min_fn() if node.min_fn else node.min
                hi = node.max_fn() if node.max_fn else node.max
                value = float(value)
                if (lo is not None and value < lo - 1e-9) or (hi is not None and value > hi + 1e-9):
                    raise CameraError(f"{name}={value} out of range [{lo}, {hi}]")
                if node.type == "int":
                    value = int(round(value))
                    if node.inc and node.inc > 1 and (value - int(lo or 0)) % int(node.inc):
                        raise CameraError(f"{name}={value} must be a multiple of {node.inc:g}")
            elif node.type == "bool":
                value = bool(value)
            node.value = value
            if node.on_set:
                node.on_set(value)
            if name == "ExposureAuto" and value == "Once":
                self._auto_once("ExposureAuto")
            if name == "GainAuto" and value == "Once":
                self._auto_once("GainAuto")

    def execute(self, name: str) -> None:
        self._require_open()
        node = self._nodes.get(name)
        if node is None or node.type != "command":
            raise CameraError(f"{name} is not a command")
        with self._lock:
            if node.lock and (reason := node.lock()):
                raise CameraError(f"{name} is not executable: {reason}")
            assert node.command is not None
            node.command()

    def feature_tree(self) -> list[tuple[str, list[str]]]:
        cats: dict[str, list[str]] = {}
        for node in self._nodes.values():
            cats.setdefault(node.category, []).append(node.name)
        return list(cats.items())

    def _require_open(self) -> None:
        if not self._open:
            raise CameraError("Camera is not open")

    # --------------------------------------------------------- auto modes
    def _auto_once(self, which: str) -> None:
        # Pretend the auto algorithm converged instantly, then revert to Off.
        self._run_auto(self._meter(self._render(self._render_params())), which, strength=1.0)
        self._nodes[which].value = "Off"

    def _meter(self, img: np.ndarray) -> float:
        """Brightness metric for the auto modes: the 99.9th percentile as a
        fraction of full scale (a mean would be dominated by the dark
        background and drive the spot into saturation)."""
        return float(np.percentile(img[::4, ::4], 99.9)) / self._max_value(self._v("PixelFormat"))

    def _run_auto(self, level: float, which: str, strength: float = 0.3) -> None:
        target = 0.7
        ratio = (target / max(level, 1e-3)) ** strength
        if which == "ExposureAuto":
            node = self._nodes["ExposureTime"]
            node.value = float(np.clip(node.value * ratio, 4.0, min(self._exposure_max(), 100_000.0)))
        else:
            node = self._nodes["Gain"]
            node.value = float(np.clip(node.value + 20 * math.log10(ratio), 0.0, node.max))

    # ---------------------------------------------------------- rendering
    @staticmethod
    def _max_value(pixel_format: str) -> int:
        return {"Mono8": 255, "Mono12p": 4095, "Mono16": 65535}[pixel_format]

    def _render_params(self) -> dict[str, Any]:
        names = ["Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical",
                 "BinningHorizontalMode", "BinningVerticalMode", "DecimationHorizontal",
                 "DecimationVertical", "ExposureTime", "Gain", "BlackLevel", "GammaEnable",
                 "Gamma", "PixelFormat", "AdcBitDepth", "ReverseX", "ReverseY"]
        p = {n: self._v(n) for n in names}
        p["t"] = time.perf_counter() - self._t0
        return p

    _NOISE_PAD = 64

    def _noise(self, h: int, w: int) -> np.ndarray:
        """A (h, w) view of standard-normal noise, randomly picked from a small
        pre-generated pool (generating fresh noise costs ~35 ms per frame)."""
        if not hasattr(self, "_noise_pool"):
            shape = (SENSOR_H + self._NOISE_PAD, SENSOR_W + self._NOISE_PAD)
            self._noise_pool = [self._rng.standard_normal(shape, dtype=np.float32) for _ in range(2)]
        pool = self._noise_pool[self._rng.integers(len(self._noise_pool))]
        oy, ox = self._rng.integers(self._NOISE_PAD, size=2)
        return pool[oy:oy + h, ox:ox + w]

    def _render(self, p: dict[str, Any]) -> np.ndarray:
        bh, bv = p["BinningHorizontal"], p["BinningVertical"]
        dh, dv = p["DecimationHorizontal"], p["DecimationVertical"]
        sx, sy = bh * dh, bv * dv  # sensor pixels per output pixel
        w, h = p["Width"], p["Height"]

        # Output pixel centres in sensor coordinates (before reverse).
        xs = (p["OffsetX"] + np.arange(w, dtype=np.float32) + 0.5) * sx
        ys = (p["OffsetY"] + np.arange(h, dtype=np.float32) + 0.5) * sy

        exp_ms = p["ExposureTime"] / 1000.0
        # Binning: Sum adds bh*bv sensor pixels; Average keeps the level but lowers noise.
        n_sum = (bh if p["BinningHorizontalMode"] == "Sum" else 1) * \
                (bv if p["BinningVerticalMode"] == "Sum" else 1)
        n_avg = (bh * bv) / n_sum
        adc_bits = 12 if p["AdcBitDepth"] == "Bit12" else 10
        adc_max = (1 << adc_bits) - 1
        fmt = p["PixelFormat"]
        out_max = self._max_value(fmt)
        # Work directly in output units: DN per electron, scaled to the pixel
        # format (a 10-bit ADC in Mono16 still spans 0..65535, coarsely).
        k = 10 ** (p["Gain"] / 20) * (adc_max / FULL_WELL_E) * (out_max / adc_max)
        black = p["BlackLevel"] / 100 * out_max

        def to_dn(signal_e: np.ndarray, noise: np.ndarray) -> np.ndarray:
            s = np.minimum(signal_e, FULL_WELL_E) * n_sum
            sigma = np.sqrt(s + n_sum * READ_NOISE_E ** 2) / math.sqrt(n_avg)
            return (s + sigma * noise) * k + black

        # Uniform background everywhere: two in-place passes over a
        # pre-generated noise pool keep full-frame simulation at camera speed.
        noise = self._noise(h, w)
        bg_e = 15.0 * exp_ms
        s_bg = min(bg_e, FULL_WELL_E) * n_sum
        sigma_bg = math.sqrt(s_bg + n_sum * READ_NOISE_E ** 2) / math.sqrt(n_avg)
        dn = np.multiply(noise, np.float32(sigma_bg * k))
        dn += np.float32(s_bg * k + black)

        # ... plus an orbiting elliptical rotated Gaussian spot, evaluated only
        # inside its bounding box.
        t = p["t"]
        cx = SENSOR_W / 2 + 300 * math.cos(t * 0.15)
        cy = SENSOR_H / 2 + 200 * math.sin(t * 0.15)
        sig_a, sig_b, theta = 60.0, 35.0, math.radians(25)
        peak_e = 600.0 * exp_ms
        half = 5 * sig_a
        ix = np.nonzero((xs > cx - half) & (xs < cx + half))[0]
        iy = np.nonzero((ys > cy - half) & (ys < cy + half))[0]
        if ix.size and iy.size:
            dx = xs[ix][None, :] - cx
            dy = ys[iy][:, None] - cy
            c, s = math.cos(theta), math.sin(theta)
            u = c * dx + s * dy
            v = -s * dx + c * dy
            spot = bg_e + peak_e * np.exp(-0.5 * ((u / sig_a) ** 2 + (v / sig_b) ** 2))
            box = (slice(iy[0], iy[-1] + 1), slice(ix[0], ix[-1] + 1))
            dn[box] = to_dn(spot.astype(np.float32), noise[box])

        np.clip(dn, 0, out_max, out=dn)
        if p["GammaEnable"]:
            dn = out_max * (dn / out_max) ** p["Gamma"]
        img = dn.astype(np.uint8 if fmt == "Mono8" else np.uint16)
        if p["ReverseX"]:
            img = img[:, ::-1]
        if p["ReverseY"]:
            img = img[::-1, :]
        return np.ascontiguousarray(img)

    def grab(self, timeout_ms: int) -> Frame | None:
        if not self._streaming:
            return None
        deadline = time.perf_counter() + timeout_ms / 1000.0
        with self._lock:
            triggered = self._v("TriggerMode") == "On"
            source = self._v("TriggerSource")
            fps = self._resulting_frame_rate()
        if triggered:
            if source != "Software":
                # No hardware lines in the simulator: an external trigger never arrives.
                time.sleep(timeout_ms / 1000.0)
                return None
            if not self._trigger_event.wait(timeout_ms / 1000.0) or not self._streaming:
                return None
            self._trigger_event.clear()
        else:
            now = time.perf_counter()
            wait = self._next_frame_t - now
            if wait > deadline - now:
                time.sleep(max(0.0, deadline - now))
                return None
            if wait > 0:
                time.sleep(wait)
            self._next_frame_t = max(self._next_frame_t + 1.0 / fps, time.perf_counter() - 1.0 / fps)
        if not self._streaming:
            return None
        with self._lock:
            params = self._render_params()
            fmt = params["PixelFormat"]
        img = self._render(params)
        with self._lock:
            for which in ("ExposureAuto", "GainAuto"):
                if self._v(which) == "Continuous":
                    self._run_auto(self._meter(img), which)
        self._frame_id += 1
        return Frame(img, self._frame_id, time.perf_counter() - self._t0, fmt, self._max_value(fmt))
