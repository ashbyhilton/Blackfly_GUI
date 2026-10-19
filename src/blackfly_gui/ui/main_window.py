"""Main application window."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QToolBar,
    QWidget,
)

from .. import __version__
from ..analysis.reference import MODES
from ..snapshot import default_path, save_snapshot
from ..camera import CameraBackend, CameraError, DeviceInfo, Frame, discover
from ..camera.controller import CameraController
from .analysis import AnalysisDock
from .analysis_tools import GaussianTab, ReferenceTab
from .image_view import ImageView
from .panels.acquisition import AcquisitionPanel
from .panels.features import FeaturesPanel
from .panels.format import FormatPanel
from .panels.trigger import TriggerPanel
from .panels.usersets import UserSetsPanel


HELP_HTML = """<h3>Blackfly GUI {version}</h3>
<p>Open-source GUI for Teledyne FLIR Spinnaker cameras.</p>
<table>
<tr><td><b>Space</b></td><td>start / stop acquisition</td></tr>
<tr><td><b>T</b></td><td>software trigger (TriggerMode On, Source Software)</td></tr>
<tr><td><b>Ctrl+S</b></td><td>snapshot to the snapshot folder</td></tr>
<tr><td><b>Ctrl+Shift+S</b></td><td>snapshot, choose file name</td></tr>
<tr><td><b>Double-click image</b></td><td>fit image to window</td></tr>
<tr><td><b>Mouse wheel / drag</b></td><td>zoom / pan the image</td></tr>
</table>
<p>Greyed-out controls show <i>why</i> in their tooltip. Most "locked" camera features are
handled automatically (auto modes switched off, acquisition paused briefly for ROI/binning).</p>
<p>Snapshots: raw camera data as lossless TIFF plus a JSON file with every camera setting.</p>
"""


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False, serial: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"Blackfly GUI {__version__}")
        self.resize(1400, 900)
        self.settings = QSettings()

        self.controller = CameraController(self)
        self.image_view = ImageView()
        self.setCentralWidget(self.image_view)
        self._devices: list[tuple[DeviceInfo, Callable[[], CameraBackend]]] = []
        self._docks: list[QDockWidget] = []

        self._last_display = None
        self._build_toolbar()
        self._build_statusbar()
        self._build_panels()
        self._restore_layout()

        c = self.controller
        c.frame_ready.connect(self._on_frame)
        c.opened.connect(self._on_opened)
        c.closed.connect(self._on_closed)
        c.streaming_changed.connect(self._on_streaming_changed)
        c.message.connect(lambda text: self.statusBar().showMessage(text, 10000))

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(500)

        self._update_actions()
        self.refresh_devices()
        if simulate:
            self._select_device(lambda d: d.interface == "Simulated")
            self.connect_camera()
        elif serial:
            if self._select_device(lambda d: d.serial == serial):
                self.connect_camera()
            else:
                self.statusBar().showMessage(f"No camera with serial {serial} found", 10000)

    # ------------------------------------------------------------ layout
    def _build_toolbar(self) -> None:
        tb = QToolBar("Camera")
        tb.setObjectName("camera_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(320)
        self.device_combo.setToolTip("Cameras found on this computer")
        tb.addWidget(QLabel(" Camera: "))
        tb.addWidget(self.device_combo)

        self.act_refresh = QAction("Refresh", self)
        self.act_refresh.setToolTip("Search for cameras again")
        self.act_refresh.triggered.connect(self.refresh_devices)
        tb.addAction(self.act_refresh)

        self.act_connect = QAction("Connect", self)
        self.act_connect.setCheckable(True)
        self.act_connect.triggered.connect(self._toggle_connect)
        tb.addAction(self.act_connect)
        tb.addSeparator()

        self.act_stream = QAction("Start", self)
        self.act_stream.setCheckable(True)
        self.act_stream.setShortcut(QKeySequence(Qt.Key.Key_Space))
        self.act_stream.setToolTip("Start / stop live acquisition (Space)")
        self.act_stream.triggered.connect(self._toggle_stream)
        tb.addAction(self.act_stream)
        tb.addSeparator()

        self.act_snap = QAction("Snapshot", self)
        self.act_snap.setShortcut(QKeySequence("Ctrl+S"))
        self.act_snap.setToolTip("Save the current frame as TIFF + settings JSON to the snapshot "
                                 "folder (Ctrl+S)")
        self.act_snap.triggered.connect(lambda: self.save_snapshot(ask=False))
        tb.addAction(self.act_snap)
        self.act_snap_as = QAction("Save as…", self)
        self.act_snap_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_snap_as.setToolTip("Save the current frame, choosing the file name (Ctrl+Shift+S)")
        self.act_snap_as.triggered.connect(lambda: self.save_snapshot(ask=True))
        tb.addAction(self.act_snap_as)
        act_folder = QAction("Folder…", self)
        act_folder.setToolTip("Choose / open the snapshot folder")
        act_folder.triggered.connect(self._snapshot_folder_menu)
        tb.addAction(act_folder)
        tb.addSeparator()
        act_help = QAction("Help", self)
        act_help.setShortcut(QKeySequence("F1"))
        act_help.triggered.connect(self._show_help)
        tb.addAction(act_help)
        self.toolbar = tb

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_res = QLabel("")
        self.lbl_fps = QLabel("")
        self.lbl_temp = QLabel("")
        for lbl in (self.lbl_res, self.lbl_fps, self.lbl_temp):
            lbl.setContentsMargins(8, 0, 8, 0)
            sb.addPermanentWidget(lbl)

    def _build_panels(self) -> None:
        c = self.controller
        self.acquisition_panel = AcquisitionPanel(c)
        self.trigger_panel = TriggerPanel(c)
        self.format_panel = FormatPanel(c)
        self.usersets_panel = UserSetsPanel(c)
        self.features_panel = FeaturesPanel(c)
        first = self.add_dock("Acquisition", _scroll(self.acquisition_panel))
        self.add_dock("Trigger", _scroll(self.trigger_panel), tabify_with=first)
        self.add_dock("Format", _scroll(self.format_panel), tabify_with=first)
        self.add_dock("User sets", _scroll(self.usersets_panel), tabify_with=first)
        self.add_dock("All features", self.features_panel, tabify_with=first)
        first.raise_()
        self.analysis = AnalysisDock(c, self.image_view)
        self.gaussian_tab = GaussianTab(self.analysis.ctx, self.analysis.aroi, self.image_view, c)
        self.reference_tab = ReferenceTab(self.analysis.ctx, c)
        self.analysis.add_analysis_tab(self.gaussian_tab, "Gaussian fit")
        self.analysis.add_analysis_tab(self.reference_tab, "Reference")
        self.analysis_dock = self.add_dock("Analysis", self.analysis, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.resizeDocks([self.analysis_dock], [260], Qt.Orientation.Vertical)
        QShortcut(QKeySequence("T"), self, activated=self._software_trigger)

    def _software_trigger(self) -> None:
        c = self.controller
        if c.is_open and c.get("TriggerMode") == "On" and c.get("TriggerSource") == "Software":
            c.execute("TriggerSoftware")

    def add_dock(self, title: str, widget: QWidget,
                 area: Qt.DockWidgetArea = Qt.DockWidgetArea.RightDockWidgetArea,
                 tabify_with: QDockWidget | None = None) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName("dock_" + "".join(ch if ch.isalnum() else "_" for ch in title.lower()))
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        if tabify_with is not None:
            self.tabifyDockWidget(tabify_with, dock)
        self._docks.append(dock)
        return dock

    # ----------------------------------------------------------- devices
    def refresh_devices(self) -> None:
        if self.controller.is_open:
            return
        self._devices, warning = discover()
        self.device_combo.clear()
        for dev, _ in self._devices:
            self.device_combo.addItem(dev.label)
        last = self.settings.value("last_serial", "")
        self._select_device(lambda d: d.serial == last)
        if warning:
            self.statusBar().showMessage(warning, 15000)
        real = sum(1 for d, _ in self._devices if d.interface != "Simulated")
        if not warning:
            self.statusBar().showMessage(f"Found {real} camera(s)", 5000)

    def _select_device(self, pred: Callable[[DeviceInfo], bool]) -> bool:
        for i, (dev, _) in enumerate(self._devices):
            if pred(dev):
                self.device_combo.setCurrentIndex(i)
                return True
        return False

    def connect_camera(self) -> None:
        idx = self.device_combo.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, "No camera", "No camera selected. Click Refresh.")
            self._update_actions()
            return
        dev, factory = self._devices[idx]
        try:
            self.controller.open(factory(), dev.serial)
        except CameraError as exc:
            QMessageBox.critical(self, "Could not open camera",
                                 f"{exc}\n\nIs the camera in use by another program (e.g. SpinView)?")
            self._update_actions()
            return
        self.settings.setValue("last_serial", dev.serial)
        self.controller.start()

    def _toggle_connect(self, checked: bool) -> None:
        if checked:
            self.connect_camera()
        else:
            self.controller.close()

    def _toggle_stream(self, checked: bool) -> None:
        if checked:
            self.controller.start()
        else:
            self.controller.stop()
        self._update_actions()

    # ------------------------------------------------------------- slots
    def _on_frame(self, frame: Frame) -> None:
        display = self.process_frame(frame)
        self.image_view.set_image(display, frame.max_value, raw=frame.image)
        self.analysis.update_frame(display, frame.image, frame.max_value, frame.frame_id)
        h, w = frame.shape
        self.lbl_res.setText(f"{w} x {h}  {frame.pixel_format}")

    def process_frame(self, frame: Frame):
        """Reference capture/correction; returns the image to display and analyse."""
        self._last_display = self.reference_tab.process(frame.image)
        return self._last_display

    # ---------------------------------------------------------- snapshots
    def snapshot_dir(self) -> Path:
        default = Path.home() / "Pictures" / "BlackflyGUI"
        return Path(self.settings.value("snapshot_dir", str(default)))

    def save_snapshot(self, ask: bool = False) -> list[Path]:
        frame = self.controller.last_frame
        if frame is None:
            self.statusBar().showMessage("No frame to save yet", 5000)
            return []
        path = default_path(self.snapshot_dir(), self.controller)
        if ask:
            chosen, _ = QFileDialog.getSaveFileName(self, "Save snapshot", str(path), "TIFF (*.tif *.tiff)")
            if not chosen:
                return []
            path = Path(chosen)
        mode = self.reference_tab.manager.mode
        corrected = self._last_display if mode != "off" else None
        try:
            files = save_snapshot(path, frame, self.controller, corrected, MODES.get(mode, ""))
        except OSError as exc:
            QMessageBox.critical(self, "Could not save snapshot", str(exc))
            return []
        extra = " (+ corrected)" if len(files) > 2 else ""
        self.statusBar().showMessage(f"Saved {files[0]}{extra}", 10000)
        return files

    def _snapshot_folder_menu(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Snapshot folder")
        box.setText(f"Snapshots are saved to:\n{self.snapshot_dir()}")
        open_b = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        change_b = box.addButton("Change…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_b:
            self.snapshot_dir().mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.snapshot_dir())))
        elif box.clickedButton() is change_b:
            folder = QFileDialog.getExistingDirectory(self, "Snapshot folder", str(self.snapshot_dir()))
            if folder:
                self.settings.setValue("snapshot_dir", folder)

    def _show_help(self) -> None:
        QMessageBox.about(self, "Blackfly GUI", HELP_HTML.format(version=__version__))

    # -------------------------------------------------------------- layout
    def _restore_layout(self) -> None:
        geom = self.settings.value("window_geometry")
        state = self.settings.value("window_state")
        if geom is not None:
            self.restoreGeometry(geom)
        if state is not None:
            self.restoreState(state)

    def _on_opened(self, dev: DeviceInfo) -> None:
        self.setWindowTitle(f"Blackfly GUI {__version__} - {dev.label}")
        self._update_actions()

    def _on_closed(self) -> None:
        self.setWindowTitle(f"Blackfly GUI {__version__}")
        self.image_view.clear()
        self.lbl_res.setText("")
        self.lbl_fps.setText("")
        self.lbl_temp.setText("")
        self._update_actions()

    def _on_streaming_changed(self, _streaming: bool) -> None:
        self._update_actions()

    def _update_actions(self) -> None:
        is_open = self.controller.is_open
        self.act_connect.setChecked(is_open)
        self.act_connect.setText("Disconnect" if is_open else "Connect")
        self.device_combo.setEnabled(not is_open)
        self.act_refresh.setEnabled(not is_open)
        self.act_stream.setEnabled(is_open)
        self.act_snap.setEnabled(is_open)
        self.act_snap_as.setEnabled(is_open)
        self.act_stream.setChecked(self.controller.is_streaming)
        self.act_stream.setText("Stop" if self.controller.is_streaming else "Start")

    def _update_stats(self) -> None:
        c = self.controller
        if not c.is_open:
            return
        if c.is_streaming:
            cam_fps, disp_fps = c.measured_rates()
            text = f"camera {cam_fps:5.1f} fps | display {disp_fps:5.1f} fps"
            assert c.backend is not None
            stats = c.backend.stream_stats()
            lost = int(stats.get("dropped", 0) + stats.get("lost", 0) + stats.get("incomplete", 0))
            if lost:
                text += f" | {lost} dropped"
            self.lbl_fps.setText(text)
        else:
            self.lbl_fps.setText("stopped")
        temp = c.get("DeviceTemperature")
        if temp is not None:
            self.lbl_temp.setText(f"{temp:.1f} °C")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        self.controller.close()
        super().closeEvent(event)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setMinimumWidth(widget.sizeHint().width() + 24)
    return area
