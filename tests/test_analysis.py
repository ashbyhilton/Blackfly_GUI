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


def test_projection_fits_match_2d_widths():
    from blackfly_gui.analysis.profile1d import fit_gaussian_1d, projections
    t = math.radians(30.0)
    sx, sy = math.hypot(30 * math.cos(t), 12 * math.sin(t)), math.hypot(30 * math.sin(t), 12 * math.cos(t))
    # noiseless: exact; faint and noisy (peak SNR 5 per pixel): ~0.5 px scatter
    for noise, abs_tol, rel_tol in ((0, 1e-3, 1e-4), (10, 2.0, 0.1)):
        img = synth((300, 400), 50, 210.4, 140.7, 30.0, 12.0, 30.0, 20, noise=noise)
        fx, fy = (fit_gaussian_1d(p) for p in projections(img))
        assert fx.success and fy.success
        assert fx.centre == pytest.approx(210.4, abs=abs_tol)
        assert fy.centre == pytest.approx(140.7, abs=abs_tol)
        assert fx.sigma == pytest.approx(sx, rel=rel_tol)
        assert fy.sigma == pytest.approx(sy, rel=rel_tol)
    assert not fit_gaussian_1d(np.full(50, 3.0)).success


def test_spot_centroid_ignores_hot_pixel_and_nan():
    from blackfly_gui.analysis.alignment import spot_centroid
    img = synth((300, 400), 30, 150.3, 120.6, 8.0, 8.0, 0.0, 10)  # weak spot
    img[220, 250] = 255.0  # hot pixel, much brighter than the spot, ~140 px away
    img[5, 5] = np.nan
    cx, cy = spot_centroid(img)
    assert (cx, cy) == (pytest.approx(150.3, abs=0.05), pytest.approx(120.6, abs=0.05))
    noisy = synth((300, 400), 30, 150.3, 120.6, 8.0, 8.0, 0.0, 10, noise=3)
    cx, cy = spot_centroid(noisy)
    assert (cx, cy) == (pytest.approx(150.3, abs=0.3), pytest.approx(120.6, abs=0.3))
    assert spot_centroid(np.full((50, 50), 10.0)) is None
    assert spot_centroid(synth((300, 400), 0, 0, 0, 1, 1, 0, 10, noise=3)) is None  # noise only
    focused = synth((300, 400), 200, 77.4, 201.7, 1.2, 1.2, 0.0, 10, noise=3)
    cx, cy = spot_centroid(focused)
    assert (cx, cy) == (pytest.approx(77.4, abs=0.2), pytest.approx(201.7, abs=0.2))


def test_geometry_round_trip():
    from blackfly_gui.analysis.alignment import Geometry
    g = Geometry(width=512, height=256, offset_x=100, offset_y=40, step_x=2, step_y=2,
                 reverse_x=True)
    sx, sy = g.to_sensor(10.5, 20.5)
    assert (sx, sy) == ((100 + 512 - 10.5) * 2, (40 + 20.5) * 2)
    assert g.to_image(sx, sy) == pytest.approx((10.5, 20.5))


def test_running_average():
    from blackfly_gui.analysis.alignment import RunningAverage
    avg = RunningAverage()
    frame = np.full((4, 4), 7, dtype=np.uint8)
    assert avg.process(frame) is frame  # n = 1: off, no copy
    avg.n = 4
    out = [avg.process(np.full((4, 4), v, dtype=np.uint8)) for v in (0, 4, 8)]
    assert out[-1][0, 0] == pytest.approx(4.0)  # plain mean while fewer than n frames
    for _ in range(50):
        last = avg.process(np.full((4, 4), 100, dtype=np.uint8))
    assert last[0, 0] == pytest.approx(100, abs=0.01)
    assert avg.process(np.zeros((2, 2), dtype=np.uint8)).shape == (2, 2)  # resets on new size
