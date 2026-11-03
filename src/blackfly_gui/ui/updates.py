"""Tell the user when a newer release is on GitHub.

The check runs once, a few seconds after start-up, on a background thread
(network only: the live view never waits for it) and fails silently when
offline. If a newer release exists, an "Update available" button appears in
the status bar; nothing pops up in the middle of an experiment. Help has a
"Check for updates…" button and a box to turn the start-up check off.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..updates import RELEASES_PAGE, Release, UpdateError, fetch_latest, is_newer

SETTING = "check_updates_at_startup"
STARTUP_DELAY_MS = 3000  # let the camera connect first

HOW_TO_STANDALONE = (
    "Download the zip, unzip it next to this version and run <b>BlackflyGUI.exe</b> from "
    "the new folder. Your app settings carry over. Before deleting the old folder, move "
    "out any files you saved inside it."
)
HOW_TO_SOURCE = "You are running from source: update with <code>git pull</code>."


class UpdateChecker(QObject):
    _done = Signal(bool, object, str)  # manual, Release or None, error message

    def __init__(self, window: QMainWindow,
                 fetch: Callable[[], Release] = fetch_latest) -> None:
        super().__init__(window)
        self.window = window
        self._fetch = fetch
        self._busy = False
        self.available: Release | None = None
        self.button: QPushButton | None = None
        self._done.connect(self._on_done)

    # ------------------------------------------------------------ setting
    @property
    def check_at_startup(self) -> bool:
        return QSettings().value(SETTING, True, type=bool)

    def set_check_at_startup(self, on: bool) -> None:
        QSettings().setValue(SETTING, bool(on))

    # -------------------------------------------------------------- check
    def check_soon(self) -> None:
        """Start-up check (called from main(), so tests never go online)."""
        if not self.check_at_startup:
            return
        timer = QTimer(self)  # child: never fires after the window has gone
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.check(manual=False))
        timer.start(STARTUP_DELAY_MS)

    def check(self, manual: bool = True) -> None:
        """manual=True reports every outcome; otherwise only a newer release."""
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._run, args=(manual,), name="update-check",
                         daemon=True).start()  # daemon: closing the app never waits

    def _run(self, manual: bool) -> None:
        # Background thread. The Qt signal hands the result to the GUI thread.
        try:
            release, error = self._fetch(), ""
        except UpdateError as exc:
            release, error = None, str(exc)
        except Exception as exc:  # an update check must never take the app down
            release, error = None, f"Unexpected error: {exc}"
        try:
            self._done.emit(manual, release, error)
        except RuntimeError:  # window closed while we were waiting
            pass

    def _on_done(self, manual: bool, release: Release | None, error: str) -> None:
        self._busy = False
        if release is None:
            if manual:
                self._show_error(error)
            return
        if is_newer(release.version, __version__):
            self.available = release
            self._show_button()
            if manual:
                self.show_dialog()
        elif manual:
            QMessageBox.information(self.window, "Check for updates",
                                    f"You have the latest version ({__version__}).")

    # ---------------------------------------------------------------- UI
    def _show_button(self) -> None:
        assert self.available is not None
        if self.button is None:
            self.button = QPushButton()
            self.button.setFlat(True)
            self.button.setStyleSheet("font-weight: bold;")
            self.button.setToolTip("A newer version has been released - click for details")
            self.button.clicked.connect(self.show_dialog)
            self.window.statusBar().insertPermanentWidget(0, self.button)
        self.button.setText(f"Update available: {self.available.version}")

    def show_dialog(self) -> None:
        if self.available is not None:
            UpdateDialog(self.available, self.window).exec()

    def _show_error(self, error: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, "Check for updates",
                          f"Could not check for updates.\n\n{error}", parent=self.window)
        page = box.addButton("Open releases page", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(RELEASES_PAGE))


class UpdateDialog(QDialog):
    """Release notes, how to update, and a Download button."""

    def __init__(self, release: Release, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.release = release
        self.setWindowTitle("Update available")
        layout = QVBoxLayout(self)

        title = QLabel(f"<b>Blackfly GUI {release.version}</b> is available (you have "
                       f"{__version__}). <a href=\"{release.page_url}\">Release page</a>")
        title.setOpenExternalLinks(True)
        layout.addWidget(title)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or "*No release notes.*")
        layout.addWidget(notes)

        how = QLabel(HOW_TO_STANDALONE if getattr(sys, "frozen", False) else HOW_TO_SOURCE)
        how.setWordWrap(True)
        layout.addWidget(how)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        label = "Download" if release.download_url else "Open release page"
        self.download_button = buttons.addButton(label, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.download)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, 420)

    def download(self) -> None:
        # The browser does the download; we never run or unpack anything.
        QDesktopServices.openUrl(QUrl(self.release.download_url or self.release.page_url))
        self.accept()
