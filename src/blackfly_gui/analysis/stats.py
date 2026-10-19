"""Region statistics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RegionStats:
    mean: float
    std: float
    min: float
    max: float
    sum: float
    n_pixels: int
    n_saturated: int

    def rows(self) -> list[tuple[str, str]]:
        return [
            ("Mean", f"{self.mean:.4g}"),
            ("Std dev", f"{self.std:.4g}"),
            ("Min", f"{self.min:.6g}"),
            ("Max", f"{self.max:.6g}"),
            ("Sum", f"{self.sum:.6g}"),
            ("Pixels", f"{self.n_pixels}"),
            ("Saturated", f"{self.n_saturated} ({100 * self.n_saturated / max(self.n_pixels, 1):.2f} %)"),
        ]


def region_stats(region: np.ndarray, raw_region: np.ndarray | None = None,
                 max_value: float | None = None) -> RegionStats:
    """Statistics of ``region`` (finite values only). Saturation is counted
    on ``raw_region`` (the uncorrected camera data) when given."""
    data = region.astype(np.float64, copy=False)
    if np.issubdtype(region.dtype, np.floating):
        data = data[np.isfinite(data)]
    if data.size == 0:
        return RegionStats(np.nan, np.nan, np.nan, np.nan, 0.0, 0, 0)
    sat_src = raw_region if raw_region is not None else region
    n_sat = int(np.count_nonzero(sat_src >= max_value)) if max_value is not None else 0
    return RegionStats(float(data.mean()), float(data.std()), float(data.min()), float(data.max()),
                       float(data.sum()), int(data.size), n_sat)
