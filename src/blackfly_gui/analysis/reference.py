"""Reference image correction: dark subtraction, background subtraction,
division, and flat-field correction.

References are averages of N camera frames (reducing their noise by
sqrt(N)), stored as float32 and saveable as TIFF.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

MODES = {
    "off": "Off (raw image)",
    "sub_dark": "Subtract dark:  I − D",
    "sub_ref": "Subtract reference:  I − R",
    "div_ref": "Divide by reference:  I / R",
    "flatfield": "Flat-field:  (I − D) / (R − D) × mean(R − D)",
}
NEEDS = {"off": (), "sub_dark": ("dark",), "sub_ref": ("ref",), "div_ref": ("ref",),
         "flatfield": ("dark", "ref")}


@dataclass
class Capture:
    target: str  # 'dark' or 'ref'
    n: int
    accum: np.ndarray | None = None
    count: int = 0


class ReferenceManager:
    def __init__(self) -> None:
        self.images: dict[str, np.ndarray | None] = {"dark": None, "ref": None}
        self.mode = "off"
        self.capture: Capture | None = None
        self.warning = ""
        self.eps = 1e-6

    # -------------------------------------------------------- capture
    def start_capture(self, target: str, n: int) -> None:
        self.capture = Capture(target, max(1, int(n)))

    def feed(self, image: np.ndarray) -> str | None:
        """Accumulate a raw frame if capturing. Returns the target name when a
        capture completes."""
        cap = self.capture
        if cap is None:
            return None
        if cap.accum is not None and cap.accum.shape != image.shape:
            self.capture = None
            self.warning = "Capture aborted: image size changed"
            return None
        if cap.accum is None:
            cap.accum = np.zeros(image.shape, dtype=np.float64)
        cap.accum += image
        cap.count += 1
        if cap.count >= cap.n:
            self.images[cap.target] = (cap.accum / cap.count).astype(np.float32)
            self.capture = None
            return cap.target
        return None

    # -------------------------------------------------------- apply
    def ready(self, mode: str | None = None) -> bool:
        mode = mode or self.mode
        return all(self.images[k] is not None for k in NEEDS[mode])

    def apply(self, image: np.ndarray) -> np.ndarray:
        """Return the corrected image (float32), or ``image`` unchanged when
        off / references missing / shapes mismatch (with :attr:`warning`)."""
        if self.mode == "off":
            self.warning = ""
            return image
        if not self.ready():
            self.warning = "Reference image(s) missing for this mode"
            return image
        refs = [self.images[k] for k in NEEDS[self.mode]]
        if any(r.shape != image.shape for r in refs):  # type: ignore[union-attr]
            self.warning = (f"Reference size {refs[0].shape[1]}x{refs[0].shape[0]} does not match "  # type: ignore[union-attr]
                            f"image {image.shape[1]}x{image.shape[0]}; showing raw image")
            return image
        self.warning = ""
        img = image.astype(np.float32)
        dark, ref = self.images["dark"], self.images["ref"]
        if self.mode == "sub_dark":
            return img - dark
        if self.mode == "sub_ref":
            return img - ref
        if self.mode == "div_ref":
            return _safe_divide(img, ref, self.eps)
        # flat-field
        denom = ref - dark  # type: ignore[operator]
        scale = float(np.mean(denom[denom > self.eps])) if np.any(denom > self.eps) else 1.0
        return _safe_divide(img - dark, denom, self.eps) * np.float32(scale)

    # --------------------------------------------------------- files
    def save(self, target: str, path: str | Path) -> None:
        img = self.images[target]
        if img is None:
            raise ValueError(f"No {target} image to save")
        tifffile.imwrite(path, img, metadata={"blackfly_gui_reference": target})

    def load(self, target: str, path: str | Path) -> np.ndarray:
        img = tifffile.imread(path).astype(np.float32)
        if img.ndim not in (2, 3):
            raise ValueError(f"Unsupported image shape {img.shape}")
        self.images[target] = img
        return img

    def clear(self, target: str) -> None:
        self.images[target] = None


def _safe_divide(num: np.ndarray, den: np.ndarray, eps: float) -> np.ndarray:
    out = np.full(num.shape, np.nan, dtype=np.float32)
    np.divide(num, den, out=out, where=np.abs(den) > eps)
    return out
