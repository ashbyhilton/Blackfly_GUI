"""Backend-independent camera interface.

Every camera backend (real Spinnaker hardware, or the simulator) implements
:class:`CameraBackend`. Features are addressed by their GenICam / SFNC names
(``ExposureTime``, ``Gain``, ``Width`` ...), so the GUI code is identical for
both backends and works with any Spinnaker camera that exposes a feature.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class CameraError(RuntimeError):
    """Raised for any camera-level failure (open, set feature, grab ...)."""


@dataclass
class DeviceInfo:
    """A camera found during enumeration."""

    serial: str
    model: str
    vendor: str = ""
    interface: str = ""

    @property
    def label(self) -> str:
        return f"{self.model} ({self.serial})"


@dataclass
class Frame:
    """One image plus the metadata needed to display / analyse it."""

    image: np.ndarray  # 2D (mono) or 3D HxWx3 (colour), uint8 or uint16
    frame_id: int
    timestamp: float  # seconds (camera clock when available)
    pixel_format: str
    max_value: int  # saturation level, e.g. 255, 4095, 65535

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape[0], self.image.shape[1]


@dataclass
class FeatureInfo:
    """Snapshot of one camera feature's state."""

    name: str
    type: str  # 'int', 'float', 'enum', 'bool', 'command', 'string'
    display_name: str = ""
    value: Any = None
    min: float | None = None
    max: float | None = None
    inc: float | None = None
    entries: list[str] = field(default_factory=list)  # enum: currently available entries
    unit: str = ""
    description: str = ""
    readable: bool = True
    writable: bool = False
    # Filled in by CameraController: whether the GUI can set this feature
    # (possibly by stopping the stream / changing a prerequisite) and a human
    # readable note explaining what will happen or why it is locked.
    settable: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name


class CameraBackend(ABC):
    """Abstract camera. All methods are called from the GUI thread except
    :meth:`grab`, which is called from the acquisition thread."""

    # --- discovery / lifetime -------------------------------------------
    @abstractmethod
    def list_devices(self) -> list[DeviceInfo]: ...

    @abstractmethod
    def open(self, serial: str | None = None) -> DeviceInfo:
        """Open a camera (the first one if ``serial`` is None)."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    # --- acquisition ------------------------------------------------------
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def is_streaming(self) -> bool: ...

    @abstractmethod
    def grab(self, timeout_ms: int) -> Frame | None:
        """Block for up to ``timeout_ms`` for the next frame; None on timeout."""

    # --- features -----------------------------------------------------------
    @abstractmethod
    def has_feature(self, name: str) -> bool: ...

    @abstractmethod
    def get_info(self, name: str) -> FeatureInfo | None:
        """Full feature description, or None if the camera lacks it."""

    @abstractmethod
    def get_value(self, name: str) -> Any: ...

    @abstractmethod
    def set_value(self, name: str, value: Any) -> None:
        """Set directly; raise :class:`CameraError` if not writable."""

    @abstractmethod
    def execute(self, name: str) -> None:
        """Execute a command feature (e.g. ``TriggerSoftware``)."""

    @abstractmethod
    def feature_tree(self) -> list[tuple[str, list[str]]]:
        """[(category display name, [feature names...]), ...] for the
        'All features' browser."""

    # --- stats (optional) ---------------------------------------------------
    def stream_stats(self) -> dict[str, float]:
        """Backend-specific counters, e.g. {'dropped': 3}."""
        return {}
