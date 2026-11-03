"""X/Y projections (sums along columns and rows) and 1D Gaussian fits.

For an elliptical 2D Gaussian the x projection is a 1D Gaussian with the
2D fit's sigma x (likewise y). Summing first makes the 1D fit robust on
faint, noisy spots where a 2D fit struggles."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


@dataclass
class Fit1D:
    success: bool
    message: str = ""
    amplitude: float = math.nan
    centre: float = math.nan  # pixel index of the profile
    sigma: float = math.nan
    offset: float = math.nan


def gaussian1d(x: np.ndarray, amp: float, x0: float, sigma: float, offset: float) -> np.ndarray:
    return amp * np.exp(-0.5 * ((x - x0) / sigma) ** 2) + offset


def projections(region: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(sum of each column, sum of each row) as float64. NaN pixels count as
    0; colour images are averaged over channels first."""
    data = region.mean(axis=2) if region.ndim == 3 else region
    if np.issubdtype(data.dtype, np.floating):
        return np.nansum(data, axis=0, dtype=np.float64), np.nansum(data, axis=1, dtype=np.float64)
    return data.sum(axis=0, dtype=np.float64), data.sum(axis=1, dtype=np.float64)


def fit_gaussian_1d(y: np.ndarray) -> Fit1D:
    """Fit ``gaussian1d`` to a profile sampled at x = 0, 1, 2, ..."""
    y = np.asarray(y, dtype=np.float64)
    finite = np.isfinite(y)
    if np.count_nonzero(finite) < 5:
        return Fit1D(False, "Too few points")
    x = np.arange(y.size, dtype=np.float64)
    x, y = x[finite], y[finite]
    bg, peak = float(np.percentile(y, 10)), float(y.max())
    if not peak > bg:
        return Fit1D(False, "No peak found")
    # Seed from thresholded moments (as the 2D fit does).
    w = y - bg
    w[w < 0.2 * (peak - bg)] = 0.0
    c = float((w * x).sum() / w.sum())
    s = math.sqrt(max(float((w * (x - c) ** 2).sum() / w.sum()), 0.25) / 0.6)
    try:
        # Levenberg-Marquardt: ~4x faster than "trf" with bounds (it runs on
        # a worker thread but still holds Python's GIL), same result.
        popt, _ = curve_fit(gaussian1d, x, y, p0=(peak - bg, c, s, bg), method="lm", maxfev=2000)
    except (RuntimeError, ValueError) as exc:
        return Fit1D(False, f"Fit did not converge: {exc}")
    amp, x0, sigma, offset = (float(v) for v in popt)
    sigma, n = abs(sigma), float(y.size)
    if not (amp > 0 and -n <= x0 <= 2 * n and 0.3 <= sigma <= 4 * n and math.isfinite(offset)):
        return Fit1D(False, "Fit did not converge")
    return Fit1D(True, "OK", amp, x0, sigma, offset)
