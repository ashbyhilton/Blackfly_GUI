"""Camera backends and discovery."""

from __future__ import annotations

from typing import Callable

from .base import CameraBackend, CameraError, DeviceInfo, FeatureInfo, Frame
from .simulated import SimulatedCamera

__all__ = [
    "CameraBackend", "CameraError", "DeviceInfo", "FeatureInfo", "Frame",
    "SimulatedCamera", "discover", "spinnaker_status",
]


def spinnaker_status() -> tuple[bool, str]:
    """(available, message). PySpin is optional so the GUI still runs in
    simulation mode on machines without the Spinnaker SDK."""
    try:
        import PySpin  # noqa: F401
    except ImportError as exc:
        return False, f"PySpin not installed ({exc}). Only the simulated camera is available."
    return True, ""


def discover(include_simulated: bool = True) -> tuple[list[tuple[DeviceInfo, Callable[[], CameraBackend]]], str]:
    """Find cameras. Returns ([(device, backend factory)], warning text)."""
    found: list[tuple[DeviceInfo, Callable[[], CameraBackend]]] = []
    warning = ""
    ok, warning = spinnaker_status()
    if ok:
        try:
            from .spinnaker import SpinnakerCamera

            for dev in SpinnakerCamera.enumerate():
                found.append((dev, SpinnakerCamera))
        except Exception as exc:  # noqa: BLE001 - never block the GUI on discovery
            warning = f"Spinnaker discovery failed: {exc}"
    if include_simulated:
        found.extend((dev, SimulatedCamera) for dev in SimulatedCamera().list_devices())
    return found, warning
