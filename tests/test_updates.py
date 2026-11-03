"""Update check: version parsing, GitHub replies and the UI. Never goes online."""

import io
import json
import threading
import urllib.error

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow, QMessageBox

from blackfly_gui import __version__, updates
from blackfly_gui.ui import updates as updates_ui
from blackfly_gui.ui.main_window import MainWindow
from blackfly_gui.updates import Release, UpdateError, is_newer, parse_release, parse_version


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))


@pytest.fixture
def opened_urls(monkeypatch):
    urls = []
    monkeypatch.setattr(updates_ui.QDesktopServices, "openUrl", lambda url: urls.append(url.toString()))
    return urls


GITHUB_REPLY = {
    "tag_name": "v9.1.0",
    "html_url": "https://github.com/ashbyhilton/Blackfly_GUI/releases/tag/v9.1.0",
    "body": "## What's new\r\n- Alignment tab",
    "assets": [
        {"name": "notes.txt", "browser_download_url": "https://example.org/notes.txt"},
        {"name": "BlackflyGUI-9.1.0-win64.zip", "browser_download_url": "https://example.org/w.zip"},
    ],
}
NEWER = Release("9.1.0", "https://example.org/page", "https://example.org/w.zip", "notes")
SAME = Release(__version__, "https://example.org/page", None, "")


# ------------------------------------------------------------ versions
@pytest.mark.parametrize("text, expected", [
    ("v0.2.0", (0, 2, 0)), ("0.10.3", (0, 10, 3)), (" V1.2", (1, 2)), ("1.2.0rc1", (1, 2, 0)),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_parse_version_rejects_non_versions():
    with pytest.raises(ValueError):
        parse_version("latest")


@pytest.mark.parametrize("candidate, current, newer", [
    ("v0.2.0", "0.1.0", True),
    ("0.1.0", "0.1.0", False),
    ("0.10.0", "0.9.9", True),   # numeric, not alphabetical
    ("0.2", "0.2.0", False),     # missing parts count as 0
    ("0.2.0.1", "0.2", True),
    ("0.1.0", "0.2.0", False),   # running a newer build than the release
])
def test_is_newer(candidate, current, newer):
    assert is_newer(candidate, current) is newer


# ------------------------------------------------------- GitHub replies
def test_parse_release_picks_the_windows_zip():
    r = parse_release(GITHUB_REPLY)
    assert r.version == "9.1.0"
    assert r.download_url == "https://example.org/w.zip"
    assert r.page_url.endswith("/v9.1.0")
    assert "Alignment tab" in r.notes


def test_parse_release_without_assets():
    r = parse_release({"tag_name": "v1.0.0", "html_url": None, "body": None, "assets": []})
    assert r.download_url is None
    assert r.page_url == updates.RELEASES_PAGE
    assert r.notes == ""


@pytest.mark.parametrize("data", [{}, [], None, {"tag_name": "nightly"}, {"tag_name": 3}])
def test_parse_release_rejects_bad_replies(data):
    with pytest.raises(UpdateError):
        parse_release(data)


def test_fetch_latest(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["url"], seen["agent"] = request.full_url, request.get_header("User-agent")
        return io.BytesIO(json.dumps(GITHUB_REPLY).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    assert updates.fetch_latest().version == "9.1.0"
    assert seen["url"] == updates.API_LATEST
    assert seen["agent"].startswith("BlackflyGUI/")


@pytest.mark.parametrize("error, message", [
    (urllib.error.HTTPError(updates.API_LATEST, 403, "rate limit", {}, None), "limiting"),
    (urllib.error.HTTPError(updates.API_LATEST, 404, "not found", {}, None), "No release"),
    (urllib.error.HTTPError(updates.API_LATEST, 500, "oops", {}, None), "500"),
    (urllib.error.URLError("no route to host"), "online"),
    (TimeoutError("timed out"), "timed out"),
])
def test_fetch_latest_errors(monkeypatch, error, message):
    def urlopen(request, timeout):
        raise error

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    with pytest.raises(UpdateError, match=message):
        updates.fetch_latest()


def test_fetch_latest_bad_json(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda r, timeout: io.BytesIO(b"<html>"))
    with pytest.raises(UpdateError):
        updates.fetch_latest()


# ------------------------------------------------------------------ UI
@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    qtbot.addWidget(w)
    return w


def make_checker(window, result):
    calls = []

    def fetch():
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    return updates_ui.UpdateChecker(window, fetch=fetch), calls


def test_startup_check_shows_button_without_popup(qtbot, window, monkeypatch):
    monkeypatch.setattr(updates_ui.UpdateDialog, "exec", lambda self: pytest.fail("no popup"))
    checker, _ = make_checker(window, NEWER)
    checker.check(manual=False)
    qtbot.waitUntil(lambda: checker.button is not None)
    assert checker.button.text() == "Update available: 9.1.0"


@pytest.mark.parametrize("result", [SAME, UpdateError("offline")])
def test_startup_check_is_silent_otherwise(qtbot, window, monkeypatch, result):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("no popup"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: pytest.fail("no popup"))
    checker, calls = make_checker(window, result)
    checker.check(manual=False)
    qtbot.waitUntil(lambda: not checker._busy)
    assert calls and checker.button is None


def test_manual_check_reports_up_to_date(qtbot, window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    checker, _ = make_checker(window, SAME)
    checker.check(manual=True)
    qtbot.waitUntil(lambda: bool(shown))
    assert __version__ in shown[0]


def test_manual_check_reports_errors(qtbot, window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    checker, _ = make_checker(window, UpdateError("GitHub is limiting requests"))
    checker.check(manual=True)
    qtbot.waitUntil(lambda: bool(shown))
    assert "limiting" in shown[0]


def test_manual_check_opens_dialog_for_new_release(qtbot, window, monkeypatch):
    opened = []
    monkeypatch.setattr(updates_ui.UpdateDialog, "exec", lambda self: opened.append(self.release))
    checker, _ = make_checker(window, NEWER)
    checker.check(manual=True)
    qtbot.waitUntil(lambda: bool(opened))
    assert opened[0] is NEWER and checker.button is not None


def test_unexpected_errors_do_not_escape(qtbot, window):
    checker, _ = make_checker(window, RuntimeError("boom"))
    checker.check(manual=False)
    qtbot.waitUntil(lambda: not checker._busy)
    assert checker.button is None


def test_one_check_at_a_time(qtbot, window):
    release = threading.Event()
    calls = []

    def slow_fetch():
        calls.append(1)
        release.wait(5)
        return SAME

    checker = updates_ui.UpdateChecker(window, fetch=slow_fetch)
    checker.check(manual=False)
    checker.check(manual=False)
    release.set()
    qtbot.waitUntil(lambda: not checker._busy)
    assert len(calls) == 1


@pytest.mark.parametrize("enabled", [True, False])
def test_check_soon_respects_setting(qtbot, window, monkeypatch, enabled):
    monkeypatch.setattr(updates_ui, "STARTUP_DELAY_MS", 0)
    checker, calls = make_checker(window, SAME)
    checker.set_check_at_startup(enabled)
    checker.check_soon()
    qtbot.wait(200)
    qtbot.waitUntil(lambda: not checker._busy)
    assert bool(calls) is enabled


def test_dialog_download_opens_zip(qtbot, opened_urls):
    dialog = updates_ui.UpdateDialog(NEWER)
    qtbot.addWidget(dialog)
    assert dialog.download_button.text() == "Download"
    dialog.download_button.click()
    assert opened_urls == ["https://example.org/w.zip"]


def test_dialog_without_zip_opens_release_page(qtbot, opened_urls):
    dialog = updates_ui.UpdateDialog(Release("9.1.0", "https://example.org/page", None, ""))
    qtbot.addWidget(dialog)
    assert dialog.download_button.text() == "Open release page"
    dialog.download_button.click()
    assert opened_urls == ["https://example.org/page"]


def test_help_has_update_controls(qtbot, monkeypatch):
    w = MainWindow()
    qtbot.addWidget(w)
    box, check_now = w.help_box()
    assert check_now.text() == "Check for updates…"
    assert box.defaultButton() is not check_now  # Enter just closes Help
    assert box.checkBox().isChecked()  # on by default
    box.checkBox().setChecked(False)
    assert w.updates.check_at_startup is False

    checked = []
    monkeypatch.setattr(w.updates, "check", lambda manual: checked.append(manual))
    monkeypatch.setattr(w, "help_box", lambda: (box, check_now))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: check_now.click())
    w._show_help()
    assert checked == [True]
