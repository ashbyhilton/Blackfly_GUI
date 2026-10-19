"""Save / load all writable camera settings to a JSON file.

Handy for sharing a known-good configuration with students: unlike camera
user sets, the file can be emailed, versioned and loaded on another camera
of the same model.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .camera.controller import CameraController

VALUE_TYPES = ("int", "float", "enum", "bool")
# Never restore these: they are actions or would fight the restore itself.
SKIP = {"UserSetSelector", "UserSetDefault", "DeviceUserID", "TriggerSoftware"}


def snapshot(controller: CameraController) -> dict[str, Any]:
    """All readable, directly writable value features in node-map order.

    Taken with the stream paused so stream-locked features (ROI, binning,
    pixel format) count as writable, while features locked by an auto mode
    (e.g. ExposureTime with ExposureAuto=Continuous) are left out --
    restoring them would silently switch the auto mode off."""
    assert controller.backend is not None
    values: dict[str, Any] = {}
    with controller.paused():
        for _cat, names in controller.backend.feature_tree():
            for name in names:
                if name in SKIP or name in values:
                    continue
                info = controller.backend.get_info(name)
                if info is None or info.type not in VALUE_TYPES or not info.readable:
                    continue
                if info.writable or controller.only_restorable_blockers(name):
                    values[name] = info.value
    return values


def save(controller: CameraController, path: str | Path) -> int:
    dev = controller.device
    data = {
        "app": "blackfly-gui",
        "saved": datetime.now().isoformat(timespec="seconds"),
        "model": dev.model if dev else "",
        "serial": dev.serial if dev else "",
        "features": snapshot(controller),
    }
    Path(path).write_text(json.dumps(data, indent=2))
    return len(data["features"])


# Features that reset or constrain others (binning resets the ROI, pixel
# format changes ranges ...) are applied first.
STRUCTURAL = ["PixelFormat", "AdcBitDepth", "BinningSelector", "BinningHorizontalMode",
              "BinningVerticalMode", "BinningHorizontal", "BinningVertical",
              "DecimationHorizontal", "DecimationVertical", "OffsetX", "OffsetY", "Width", "Height"]


def load(controller: CameraController, path: str | Path) -> tuple[int, list[str], str]:
    """Apply settings from ``path``. Returns (applied, failed names, model in file).

    Features depend on each other, so structural ones go first and then
    everything is re-checked over a few passes until all values match (a
    later write may have reset an earlier one)."""
    data = json.loads(Path(path).read_text())
    features: dict[str, Any] = data.get("features", {})
    order = [n for n in STRUCTURAL if n in features] + [n for n in features if n not in STRUCTURAL]
    with controller.paused():
        if any(n in features for n in ("OffsetX", "OffsetY")):
            # Zero offsets first so Width/Height can take their full range.
            for n in ("OffsetX", "OffsetY"):
                try:
                    controller.apply(n, 0)
                except Exception:  # noqa: BLE001
                    pass
        for _ in range(4):
            changed = False
            for name in order:
                if not _matches(controller.get(name), features[name]):
                    try:
                        controller.apply(name, features[name])
                    except Exception:  # noqa: BLE001 - retried on the next pass
                        pass
                    changed = True
            if not changed:
                break
    failed = [n for n in order if not _matches(controller.get(n), features[n])]
    controller.features_changed.emit()
    return len(features) - len(failed), sorted(failed), data.get("model", "")


def _matches(current: Any, wanted: Any) -> bool:
    if isinstance(wanted, float) and isinstance(current, (int, float)):
        return abs(current - wanted) <= 1e-6 * max(1.0, abs(wanted))
    return current == wanted
