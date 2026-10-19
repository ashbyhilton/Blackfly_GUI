"""Smoke tests driving the real widgets against the simulated camera."""

import pytest
from PySide6.QtCore import QSettings

from blackfly_gui.ui.main_window import MainWindow


@pytest.fixture
def win(qtbot, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    w = MainWindow(simulate=True)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.controller.last_frame is not None, timeout=5000)
    yield w
    w.close()


def find(panel, name):
    for form in panel.forms:
        for widget in form.widgets:
            if widget.name == name:
                return widget
    raise KeyError(name)


def test_exposure_widget_turns_off_auto(win):
    c = win.controller
    c.set("ExposureAuto", "Continuous")
    exp = find(win.acquisition_panel, "ExposureTime")
    win.acquisition_panel.refresh()
    assert exp.isEnabled()  # still settable, with an explanatory note
    assert "ExposureAuto" in exp.toolTip()
    exp.spin.setValue(5000.0)
    assert c.get("ExposureAuto") == "Off"
    assert c.get("ExposureTime") == pytest.approx(5000.0, rel=1e-3)


def test_frame_rate_limit(win, qtbot):
    c = win.controller
    fr = find(win.acquisition_panel, "AcquisitionFrameRate")
    win.acquisition_panel.refresh()
    fr.spin.setValue(5.0)
    assert c.get("AcquisitionFrameRateEnable") is True
    c.measured_rates()
    qtbot.wait(1500)
    cam_fps, _ = c.measured_rates()
    assert 3.5 < cam_fps < 6.5


def test_software_trigger_flow(win, qtbot):
    c = win.controller
    mode = find(win.trigger_panel, "TriggerMode")
    fire = find(win.trigger_panel, "TriggerSoftware")
    win.trigger_panel.refresh()
    assert not fire.isEnabled()
    c.set("TriggerMode", "On")
    win.trigger_panel.refresh()
    assert mode.combo.currentText() == "On"
    assert fire.isEnabled()
    qtbot.wait(300)  # let frames already in flight arrive
    before = c.last_frame.frame_id
    qtbot.wait(300)
    assert c.last_frame.frame_id == before  # waiting for triggers
    fire.button.click()
    qtbot.waitUntil(lambda: c.last_frame.frame_id > before, timeout=2000)


def test_single_frame_when_stopped(win, qtbot):
    c = win.controller
    c.stop()
    before = c.last_frame.frame_id
    c.snap()
    qtbot.waitUntil(lambda: not c.is_streaming and c.last_frame.frame_id > before, timeout=3000)


def test_missing_features_hidden(win):
    # The simulated mono camera has no white balance -> row hidden.
    wb = find(win.acquisition_panel, "BalanceWhiteAuto")
    assert wb.isHidden() and wb.label.isHidden()


def test_binning_quick_button_and_roi(win, qtbot):
    c = win.controller
    win.format_panel._set_binning(2)
    assert (c.get("BinningHorizontal"), c.get("BinningVertical")) == (2, 2)
    assert (c.get("Width"), c.get("Height")) == (1024, 768)
    qtbot.waitUntil(lambda: c.last_frame.image.shape == (768, 1024), timeout=3000)
    # Enlarging width via the ROI widget reduces the offset instead of failing.
    c.set_roi(800, 0, 128, 768)
    width = find(win.format_panel, "Width")
    win.format_panel.refresh()
    width.apply(1024)
    assert (c.get("Width"), c.get("OffsetX")) == (1024, 0)
    win.format_panel._center_roi()
    c.set_roi(0, 0, 512, 256)
    win.format_panel._center_roi()
    assert (c.get("OffsetX"), c.get("OffsetY")) == (256, 256)


def test_features_browser_search_and_edit(win):
    fp = win.features_panel
    fp.show()
    fp.rebuild()
    assert "ExposureTime" in fp._items
    fp.search.setText("gamma")
    visible = [n for n, it in fp._items.items() if not it.isHidden()]
    assert set(visible) == {"GammaEnable", "Gamma"}
    fp.tree.setCurrentItem(fp._items["GammaEnable"])
    assert fp.editor is not None and fp.editor.name == "GammaEnable"
    fp.editor.check.setChecked(True)
    assert win.controller.get("GammaEnable") is True


def test_analysis_stats_and_camera_roi(win, qtbot):
    a = win.analysis
    st = a.stats_tab
    st.show_roi.setChecked(True)
    a.aroi.roi.setPos((100, 200))
    a.aroi.roi.setSize((400, 300))
    a.update_frame(*_latest(win))
    a._tick()
    assert "w=400" in st.rect_label.text()
    assert st.table.item(5, 1).text() == str(400 * 300)
    st._apply_camera_roi()
    c = win.controller
    assert (c.get("OffsetX"), c.get("OffsetY"), c.get("Width"), c.get("Height")) == (100, 200, 400, 300)
    qtbot.waitUntil(lambda: c.last_frame.image.shape == (300, 400), timeout=3000)


def test_line_profile_fwhm(win):
    import numpy as np
    from blackfly_gui.ui.analysis import profile_fwhm
    x = np.linspace(-50, 50, 1001)
    y = 10 * np.exp(-x ** 2 / (2 * 8.0 ** 2)) + 2
    assert profile_fwhm(x, y) == pytest.approx(2.3548 * 8.0, rel=1e-3)
    pt = win.analysis.profile_tab
    pt.enable.setChecked(True)
    win.analysis.update_frame(*_latest(win))
    win.analysis._tick()
    assert "length" in pt.info.text()


def _latest(win):
    f = win.controller.last_frame
    return f.image, f.image, f.max_value, f.frame_id


def test_gaussian_fit_tab(win, qtbot):
    c = win.controller
    c.set("ExposureTime", 8000.0)  # spot well below saturation
    g = win.gaussian_tab
    g.enable.setChecked(True)
    qtbot.waitUntil(lambda: g.result is not None, timeout=8000)
    r = g.result
    assert r.success, (r.message, r.warnings)
    # Simulator spot: sigma 60 x 35 px, 25 degrees.
    assert r.sigma_major == pytest.approx(60, rel=0.05)
    assert r.sigma_minor == pytest.approx(35, rel=0.05)
    assert r.theta_deg == pytest.approx(25, abs=2)
    assert g.ellipse.isVisible()
    um = float(g.table.item(2, 2).text())
    assert um == pytest.approx(r.sigma_major * 3.45, rel=0.01)


def test_reference_dark_subtraction(win, qtbot):
    rt = win.reference_tab
    rt._capture("dark", 3)
    qtbot.waitUntil(lambda: rt.manager.images["dark"] is not None, timeout=5000)
    rt.mode.setCurrentIndex(rt.mode.findData("sub_dark"))
    qtbot.waitUntil(lambda: win.image_view.image is not None and win.image_view.image.dtype.kind == "f",
                    timeout=3000)
    assert rt.manager.warning == ""


def test_snapshot_writes_tiff_and_sidecar(win, tmp_path):
    import json

    import tifffile

    win.settings.setValue("snapshot_dir", str(tmp_path / "snaps"))
    files = win.save_snapshot()
    tif, side = files[0], files[-1]
    img = tifffile.imread(tif)
    assert img.shape == win.controller.last_frame.image.shape
    meta = json.loads(side.read_text())
    assert meta["camera"]["Width"] == 2048 and meta["pixel_format"] == "Mono8"
