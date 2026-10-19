"""2D elliptical Gaussian fitting.

Model (pixel-index coordinates, x = column, y = row)::

    I(x, y) = A * exp(-u^2 / (2 sa^2) - v^2 / (2 sb^2)) + offset
    u =  (x - x0) cos(t) + (y - y0) sin(t)
    v = -(x - x0) sin(t) + (y - y0) cos(t)

``t`` is the angle of the major axis measured from +x towards +y. Because
image rows increase downwards, a positive angle appears *clockwise* on
screen.

Speed: a coarse fit runs on a block-averaged copy (seeded by image moments),
then the fit is refined on a full-resolution window around the spot.
Block averaging broadens a Gaussian by ``(f^2 - 1) / 12`` px^2 in variance
(f = block size); that is removed in quadrature when the refinement window
itself had to be downsampled.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))  # 2.3548


@dataclass
class GaussianFit:
    success: bool
    message: str = ""
    amplitude: float = math.nan
    x0: float = math.nan
    y0: float = math.nan
    sigma_major: float = math.nan
    sigma_minor: float = math.nan
    theta_deg: float = math.nan  # major axis angle from +x toward +y, (-90, 90]
    offset: float = math.nan
    rms_residual: float = math.nan
    fit_time_s: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def sigma_x(self) -> float:
        t = math.radians(self.theta_deg)
        return math.sqrt((self.sigma_major * math.cos(t)) ** 2 + (self.sigma_minor * math.sin(t)) ** 2)

    @property
    def sigma_y(self) -> float:
        t = math.radians(self.theta_deg)
        return math.sqrt((self.sigma_major * math.sin(t)) ** 2 + (self.sigma_minor * math.cos(t)) ** 2)

    @property
    def ellipticity(self) -> float:
        """minor / major (1 = round)."""
        return self.sigma_minor / self.sigma_major if self.sigma_major else math.nan


def gaussian2d(xy: tuple[np.ndarray, np.ndarray], amp: float, x0: float, y0: float,
               sa: float, sb: float, theta: float, offset: float) -> np.ndarray:
    x, y = xy
    c, s = math.cos(theta), math.sin(theta)
    dx, dy = x - x0, y - y0
    u = c * dx + s * dy
    v = -s * dx + c * dy
    return amp * np.exp(-0.5 * ((u / sa) ** 2 + (v / sb) ** 2)) + offset


def _block_mean(img: np.ndarray, f: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Average f x f blocks; returns (image, x centres, y centres) in the
    original pixel-index coordinates."""
    h, w = (img.shape[0] // f) * f, (img.shape[1] // f) * f
    small = img[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    xs = np.arange(w // f) * f + (f - 1) / 2.0
    ys = np.arange(h // f) * f + (f - 1) / 2.0
    return small, xs, ys


def moments_guess(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> tuple[float, ...] | None:
    """Initial parameters from thresholded image moments."""
    bg = float(np.percentile(img, 10))
    peak = float(img.max())
    if not math.isfinite(peak) or peak <= bg:
        return None
    w = img - bg
    w[w < 0.2 * (peak - bg)] = 0.0
    total = float(w.sum())
    if total <= 0:
        return None
    X, Y = np.meshgrid(xs, ys)
    cx, cy = float((w * X).sum() / total), float((w * Y).sum() / total)
    cxx = float((w * (X - cx) ** 2).sum() / total)
    cyy = float((w * (Y - cy) ** 2).sum() / total)
    cxy = float((w * (X - cx) * (Y - cy)).sum() / total)
    evals, evecs = np.linalg.eigh(np.array([[cxx, cxy], [cxy, cyy]]))
    # Thresholding at 20 % of peak truncates the Gaussian; the second moment
    # of the kept part is ~0.6 of the true variance.
    sa = math.sqrt(max(evals[1], 0.25) / 0.6)
    sb = math.sqrt(max(evals[0], 0.25) / 0.6)
    theta = math.atan2(evecs[1, 1], evecs[0, 1])
    return peak - bg, cx, cy, sa, sb, theta, bg


def _fit(img: np.ndarray, xs: np.ndarray, ys: np.ndarray,
         p0: tuple[float, ...]) -> tuple[np.ndarray, float]:
    """Least-squares fit; returns (parameters, RMS residual of the fitted data)."""
    X, Y = np.meshgrid(xs, ys)
    data = img.astype(np.float64).ravel()
    finite = np.isfinite(data)
    coords = (X.ravel()[finite], Y.ravel()[finite])
    span = max(xs[-1] - xs[0], ys[-1] - ys[0], 1.0)
    lower = [0.0, xs[0] - span, ys[0] - span, 0.3, 0.3, -2 * math.pi, -np.inf]
    upper = [np.inf, xs[-1] + span, ys[-1] + span, 4 * span, 4 * span, 2 * math.pi, np.inf]
    p0c = [min(max(v, lo + 1e-9), hi - 1e-9) if math.isfinite(lo) or math.isfinite(hi) else v
           for v, lo, hi in zip(p0, lower, upper)]
    popt, _ = curve_fit(gaussian2d, coords, data[finite], p0=p0c, bounds=(lower, upper),
                        method="trf", maxfev=3000)
    resid = data[finite] - gaussian2d(coords, *popt)
    return popt, float(np.sqrt(np.mean(resid ** 2)))


def fit_gaussian_2d(img: np.ndarray, max_pixels: int = 40_000,
                    saturation: float | None = None) -> GaussianFit:
    """Fit an elliptical Gaussian to ``img`` (2D). Coordinates in the result
    are pixel indices of ``img`` (centre of pixel (0, 0) is (0, 0))."""
    t0 = time.perf_counter()
    if img.ndim == 3:
        img = img.mean(axis=2)
    if img.shape[0] < 5 or img.shape[1] < 5:
        return GaussianFit(False, "Region too small (need at least 5 x 5 pixels)")
    warnings: list[str] = []
    if saturation is not None and np.count_nonzero(img >= saturation):
        warnings.append("Saturated pixels: the fit underestimates the peak and overestimates the width")

    try:
        # Stage 1: coarse fit on a block-averaged copy.
        f = max(1, math.ceil(math.sqrt(img.size / max_pixels)))
        small, xs, ys = _block_mean(img, f) if f > 1 else (img, np.arange(img.shape[1], dtype=float),
                                                           np.arange(img.shape[0], dtype=float))
        p0 = moments_guess(small, xs, ys)
        if p0 is None:
            return GaussianFit(False, "No peak found (flat image)", fit_time_s=time.perf_counter() - t0)
        p, rms = _fit(small, xs, ys, p0)
        correction = (f * f - 1) / 12.0 if f > 1 else 0.0

        # Stage 2: refine at (near) full resolution in a window of +-4 sigma.
        if f > 1:
            half = 4.0 * max(abs(p[3]), abs(p[4]))
            x0i, x1i = max(0, int(p[1] - half)), min(img.shape[1], int(p[1] + half) + 1)
            y0i, y1i = max(0, int(p[2] - half)), min(img.shape[0], int(p[2] + half) + 1)
            win = img[y0i:y1i, x0i:x1i]
            if win.shape[0] >= 5 and win.shape[1] >= 5:
                f2 = max(1, math.ceil(math.sqrt(win.size / max_pixels)))
                if f2 > 1:
                    wsmall, wxs, wys = _block_mean(win, f2)
                else:
                    wsmall = win
                    wxs, wys = np.arange(win.shape[1], dtype=float), np.arange(win.shape[0], dtype=float)
                p, rms = _fit(wsmall, wxs + x0i, wys + y0i, tuple(p))
                correction = (f2 * f2 - 1) / 12.0 if f2 > 1 else 0.0
    except (RuntimeError, ValueError) as exc:
        return GaussianFit(False, f"Fit did not converge: {exc}", fit_time_s=time.perf_counter() - t0)

    amp, x0, y0, sa, sb, theta, offset = (float(v) for v in p)
    sa, sb = abs(sa), abs(sb)
    if correction:
        sa = math.sqrt(max(sa * sa - correction, 1e-6))
        sb = math.sqrt(max(sb * sb - correction, 1e-6))
    if sb > sa:
        sa, sb = sb, sa
        theta += math.pi / 2
    theta_deg = math.degrees(theta)
    theta_deg = (theta_deg + 90.0) % 180.0 - 90.0  # -> [-90, 90)
    if theta_deg == -90.0:
        theta_deg = 90.0

    if not (0 <= x0 < img.shape[1] and 0 <= y0 < img.shape[0]):
        warnings.append("Centre lies outside the region")
    if 4 * sa > max(img.shape):
        warnings.append("Spot is large compared to the region; enlarge the analysis ROI")
    return GaussianFit(True, "OK", amp, x0, y0, sa, sb, theta_deg, offset, rms,
                       time.perf_counter() - t0, warnings)
