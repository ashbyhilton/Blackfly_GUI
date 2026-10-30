"""Alignment helpers: spot centroid, image <-> sensor coordinates, and a
running average for weak signals (e.g. a dim MOT)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _moments(w: np.ndarray, x0: int, y0: int) -> tuple[float, float, float, float] | None:
    """(cx, cy, sx, sy) of weights ``w`` whose top-left pixel is (x0, y0)."""
    colsum = w.sum(axis=0, dtype=np.float64)
    rowsum = w.sum(axis=1, dtype=np.float64)
    total = float(colsum.sum())
    if total <= 0:
        return None
    xs, ys = np.arange(w.shape[1]) + x0, np.arange(w.shape[0]) + y0
    cx, cy = float(colsum @ xs) / total, float(rowsum @ ys) / total
    sx = float(colsum @ (xs - cx) ** 2) / total
    sy = float(rowsum @ (ys - cy) ** 2) / total
    return cx, cy, max(sx, 0.0) ** 0.5, max(sy, 0.0) ** 0.5


def spot_centroid(img: np.ndarray, threshold: float = 0.2) -> tuple[float, float] | None:
    """Intensity-weighted centre (x, y) of the bright spot, in pixel-index
    coordinates (centre of pixel (0, 0) is (0, 0)); None if there is no spot.

    Pixels count with weight ``I - bg`` if they are at least ``threshold`` of
    the way from the background (10th percentile) to the peak, and at least
    5 noise sigma above the median. The peak is the 4th brightest pixel of
    every 2nd row/column, so a few hot pixels cannot push a weak spot below
    the threshold, and the centre is refined in a window of +-3 RMS widths
    around the spot so far-away hot pixels do not pull it either. Returns None
    unless the peak stands 5 sigma above the median. NaN pixels are ignored.

    Speed (~15 ms for 3 MP): statistics and a coarse first pass use every 2nd
    row/column; only the window around the spot is read at full resolution."""
    data = img.mean(axis=2) if img.ndim == 3 else img
    sub = data[::2, ::2].astype(np.float32)
    values = sub.ravel()
    if np.issubdtype(data.dtype, np.floating):
        values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    bg, median = (float(v) for v in np.percentile(values[::4], [10, 50]))
    k = min(4, values.size)
    peak = float(np.partition(values, -k)[-k])
    # Neighbour differences give the noise even where the spot fills much of
    # the image (its smooth shape barely changes from pixel to pixel).
    diff = np.diff(data[::8].astype(np.float32), axis=1).ravel()
    diff = diff[np.isfinite(diff)]
    noise = 1.4826 * float(np.median(np.abs(diff))) / np.sqrt(2) if diff.size else 0.0
    if not peak > max(bg, median + 5 * noise):
        return None
    level = np.float32(max(threshold * (peak - bg), median + 5 * noise - bg))

    def weights(region: np.ndarray) -> np.ndarray:
        w = region.astype(np.float32) - np.float32(bg)
        w[~(w >= level)] = 0.0  # also zeroes NaN
        return w

    m = _moments(weights(sub), 0, 0)
    if m is None:
        return None
    cx, cy, sx, sy = (2 * v for v in m)  # sub pixel i is full pixel 2i
    for _ in range(3):
        x0, x1 = max(0, int(cx - 3 * sx - 2)), min(data.shape[1], int(cx + 3 * sx + 3))
        y0, y1 = max(0, int(cy - 3 * sy - 2)), min(data.shape[0], int(cy + 3 * sy + 3))
        refined = _moments(weights(data[y0:y1, x0:x1]), x0, y0)
        if refined is None:
            break
        cx, cy, sx, sy = refined
    return cx, cy


@dataclass
class Geometry:
    """Where the displayed image sits on the sensor, so positions can be kept
    in full-sensor pixels and survive ROI / binning changes.

    Coordinates are view coordinates (image pixel i spans [i, i + 1]). Like
    "Set camera ROI to this box", flips are taken to mirror the image within
    the ROI."""

    width: int  # displayed image size
    height: int
    offset_x: int = 0  # camera OffsetX / OffsetY (binned pixels)
    offset_y: int = 0
    step_x: int = 1  # sensor pixels per image pixel (binning x decimation)
    step_y: int = 1
    reverse_x: bool = False
    reverse_y: bool = False

    def to_sensor(self, x: float, y: float) -> tuple[float, float]:
        u = self.width - x if self.reverse_x else x
        v = self.height - y if self.reverse_y else y
        return (self.offset_x + u) * self.step_x, (self.offset_y + v) * self.step_y

    def to_image(self, sx: float, sy: float) -> tuple[float, float]:
        u = sx / self.step_x - self.offset_x
        v = sy / self.step_y - self.offset_y
        return (self.width - u if self.reverse_x else u), (self.height - v if self.reverse_y else v)


class RunningAverage:
    """Running average over about ``n`` frames (n = 1: off). The first n
    frames are averaged equally, after that older frames fade out
    exponentially, so memory stays at one frame whatever n is."""

    def __init__(self) -> None:
        self.n = 1
        self._avg: np.ndarray | None = None
        self._count = 0

    def reset(self) -> None:
        self._avg = None
        self._count = 0

    def process(self, image: np.ndarray) -> np.ndarray:
        if self.n <= 1:
            self.reset()
            return image
        if self._avg is None or self._avg.shape != image.shape:
            self.reset()
            self._avg = image.astype(np.float32)
            self._count = 1
            return self._avg
        self._count += 1
        # A new array each time: the previous one may still be on screen or
        # being read by an analysis thread (see ui/worker.py).
        self._avg = self._avg + (image - self._avg) / np.float32(min(self._count, self.n))
        return self._avg
