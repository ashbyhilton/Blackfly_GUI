"""Save the current frame as TIFF with a JSON sidecar of camera settings."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

from .camera.base import Frame
from .camera.controller import CameraController

VALUE_TYPES = ("int", "float", "enum", "bool", "string")


def camera_state(controller: CameraController) -> dict[str, Any]:
    """Every readable value feature, read without pausing acquisition."""
    if not controller.is_open:
        return {}
    assert controller.backend is not None
    state: dict[str, Any] = {}
    for _cat, names in controller.backend.feature_tree():
        for name in names:
            if name in state:
                continue
            try:
                info = controller.backend.get_info(name)
            except Exception:  # noqa: BLE001 - skip unreadable nodes
                continue
            if info is not None and info.readable and info.type in VALUE_TYPES:
                state[name] = info.value
    return state


def default_path(folder: str | Path, controller: CameraController, suffix: str = "") -> Path:
    serial = controller.device.serial if controller.device else "camera"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return Path(folder) / f"{stamp}_{serial}{suffix}.tif"


def save_snapshot(path: str | Path, frame: Frame, controller: CameraController,
                  processed: np.ndarray | None = None, processing: str = "") -> list[Path]:
    """Write ``path`` (raw camera data, lossless) plus ``path``.json. If a
    reference-corrected image is given, it is also saved as float32 TIFF
    next to it (``*_corrected.tif``). Returns the files written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "app": "blackfly-gui",
        "saved": datetime.now().isoformat(timespec="milliseconds"),
        "model": controller.device.model if controller.device else "",
        "serial": controller.device.serial if controller.device else "",
        "frame_id": frame.frame_id,
        "timestamp_s": frame.timestamp,
        "pixel_format": frame.pixel_format,
        "max_value": frame.max_value,
        "shape": list(frame.image.shape),
        "camera": camera_state(controller),
    }
    tifffile.imwrite(path, frame.image, photometric="rgb" if frame.image.ndim == 3 else "minisblack",
                     description=json.dumps({k: v for k, v in meta.items() if k != "camera"}))
    written = [path]
    if processed is not None and processed is not frame.image:
        cpath = path.with_name(path.stem + "_corrected.tif")
        tifffile.imwrite(cpath, processed.astype(np.float32))
        meta["corrected_file"] = cpath.name
        meta["correction"] = processing
        written.append(cpath)
    side = path.with_suffix(".json")
    side.write_text(json.dumps(meta, indent=2, default=str))
    written.append(side)
    return written
