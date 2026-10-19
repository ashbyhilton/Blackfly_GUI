import math

import numpy as np
import pytest

from blackfly_gui.analysis.gaussian import FWHM_PER_SIGMA, fit_gaussian_2d, gaussian2d
from blackfly_gui.analysis.reference import ReferenceManager
from blackfly_gui.analysis.stats import region_stats


def synth(shape, amp, x0, y0, sa, sb, theta_deg, offset, noise=0.0, seed=0):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    img = gaussian2d((xx, yy), amp, x0, y0, sa, sb, math.radians(theta_deg), offset)
    if noise:
        img = img + np.random.default_rng(seed).normal(0, noise, shape)
    return img


@pytest.mark.parametrize("theta", [0.0, 25.0, -60.0, 89.0])
def test_fit_recovers_parameters_small(theta):
    img = synth((120, 160), 1000, 70.3, 55.8, 12.0, 6.0, theta, 50, noise=5)
    r = fit_gaussian_2d(img)
    assert r.success, r.message
    assert r.x0 == pytest.approx(70.3, abs=0.1)
    assert r.y0 == pytest.approx(55.8, abs=0.1)
    assert r.sigma_major == pytest.approx(12.0, rel=0.02)
    assert r.sigma_minor == pytest.approx(6.0, rel=0.02)
    assert r.theta_deg == pytest.approx(theta, abs=1.0)
    assert r.amplitude == pytest.approx(1000, rel=0.02)
    assert r.offset == pytest.approx(50, abs=2)


def test_fit_full_frame_uses_downsampling_and_stays_accurate():
    img = synth((1536, 2048), 3000, 1200.0, 640.0, 40.0, 25.0, 30.0, 100, noise=10)
    r = fit_gaussian_2d(img)
    assert r.success, r.message
    assert r.x0 == pytest.approx(1200.0, abs=0.3)
    assert r.sigma_major == pytest.approx(40.0, rel=0.02)
    assert r.sigma_minor == pytest.approx(25.0, rel=0.02)
    assert r.fit_time_s < 2.0


def test_projected_widths_and_fwhm():
    img = synth((200, 200), 500, 100, 100, 10.0, 5.0, 90.0, 0)
    r = fit_gaussian_2d(img)
    assert r.sigma_x == pytest.approx(5.0, rel=0.02)
    assert r.sigma_y == pytest.approx(10.0, rel=0.02)
    assert FWHM_PER_SIGMA == pytest.approx(2.35482, rel=1e-5)


def test_fit_flat_image_fails_cleanly():
    r = fit_gaussian_2d(np.full((50, 50), 7.0))
    assert not r.success


def test_saturation_warning():
    img = np.minimum(synth((80, 80), 400, 40, 40, 6, 6, 0, 10), 255)
    r = fit_gaussian_2d(img, saturation=255)
    assert any("Saturated" in w for w in r.warnings)


def test_reference_modes():
    m = ReferenceManager()
    dark = np.full((4, 5), 10, dtype=np.uint16)
    flat = np.full((4, 5), 110, dtype=np.uint16)
    flat[0, 0] = 60
    m.start_capture("dark", 3)
    for _ in range(3):
        done = m.feed(dark)
    assert done == "dark"
    m.start_capture("ref", 1)
    m.feed(flat)
    img = np.full((4, 5), 60, dtype=np.uint16)
    m.mode = "sub_dark"
    assert np.allclose(m.apply(img), 50)
    m.mode = "div_ref"
    out = m.apply(img)
    assert out[0, 0] == pytest.approx(1.0) and out[1, 1] == pytest.approx(60 / 110)
    m.mode = "flatfield"
    out = m.apply(img)
    assert out[0, 0] == pytest.approx(2 * out[1, 1])  # pixel with half sensitivity corrected
    # size mismatch -> raw image + warning
    raw = np.zeros((2, 2), dtype=np.uint16)
    assert m.apply(raw) is raw and "match" in m.warning


def test_reference_save_load(tmp_path):
    m = ReferenceManager()
    m.images["ref"] = np.arange(12, dtype=np.float32).reshape(3, 4)
    m.save("ref", tmp_path / "r.tif")
    m2 = ReferenceManager()
    np.testing.assert_array_equal(m2.load("ref", tmp_path / "r.tif"), m.images["ref"])


def test_region_stats_ignores_nan():
    s = region_stats(np.array([[1.0, np.nan], [3.0, 5.0]], dtype=np.float32))
    assert s.mean == pytest.approx(3.0) and s.n_pixels == 3
