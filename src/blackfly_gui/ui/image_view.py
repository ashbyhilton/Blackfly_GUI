"""Live image display built on pyqtgraph (fast, zero-copy where possible)."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ImageView(QWidget):
    """Image + histogram/LUT + cursor readout + saturation overlay.

    Analysis overlays (ROI, line profile, Gaussian ellipse ...) are added to
    :attr:`view` by other components.
    """

    cursor_moved = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: np.ndarray | None = None
        self.max_value = 255.0
        self._levels_initialised = False

        self.glw = pg.GraphicsLayoutWidget()
        self.view: pg.ViewBox = self.glw.addViewBox(row=0, col=0, lockAspect=True, invertY=True)
        self.view.setMenuEnabled(True)
        self.image_item = pg.ImageItem()
        self.image_item.setAutoDownsample(False)  # mean-downsampling costs ~25 ms per 3 MP frame
        self.view.addItem(self.image_item)
        self.sat_item = pg.ImageItem()
        self.sat_item.setZValue(5)
        self.view.addItem(self.sat_item)
        self.hist = pg.HistogramLUTItem(image=self.image_item)
        self.hist.setMaximumWidth(140)
        self.glw.addItem(self.hist, row=0, col=1)

        self.auto_levels = QCheckBox("Auto levels")
        self.auto_levels.setToolTip("Continuously stretch the display to the image min/max.\n"
                                    "Uncheck to set levels manually with the histogram handles.")
        self.saturation = QCheckBox("Show saturation")
        self.saturation.setToolTip("Paint saturated pixels red.")
        self.saturation.setChecked(True)
        full = QPushButton("Full range")
        full.setToolTip("Set display levels to the full pixel range (0 .. max).")
        full.clicked.connect(self.set_full_range)
        fit = QPushButton("Fit view")
        fit.setToolTip("Zoom to show the whole image (or double-click the image).")
        fit.clicked.connect(self.fit_view)
        self.cursor_label = QLabel("")
        self.cursor_label.setMinimumWidth(220)
        self.cursor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        for w in (self.auto_levels, self.saturation, full, fit):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.cursor_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.glw, 1)

        self._cursor_xy: tuple[float, float] | None = None
        self.glw.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.glw.scene().sigMouseClicked.connect(self._on_mouse_clicked)

    # ---------------------------------------------------------- display
    def set_image(self, image: np.ndarray, max_value: float) -> None:
        """Show a new frame. ``max_value`` is the saturation level."""
        shape_changed = self.image is None or self.image.shape != image.shape
        range_changed = max_value != self.max_value
        self.image = image
        self.max_value = float(max_value)

        if self.auto_levels.isChecked():
            sub = image[::4, ::4]
            lo, hi = float(sub.min()), float(sub.max())
            if hi <= lo:
                hi = lo + 1
            self.image_item.setImage(image, autoLevels=False, levels=(lo, hi))
        else:
            self.image_item.setImage(image, autoLevels=False)
            if not self._levels_initialised or range_changed:
                self.set_full_range()
        self._levels_initialised = True

        if self.saturation.isChecked() and np.issubdtype(image.dtype, np.integer):
            mask = image >= self.max_value
            if mask.any():
                rgba = np.zeros(image.shape[:2] + (4,), dtype=np.uint8)
                rgba[mask] = (255, 0, 0, 255)
                self.sat_item.setImage(rgba, autoLevels=False)
                self.sat_item.setVisible(True)
            else:
                self.sat_item.setVisible(False)
        else:
            self.sat_item.setVisible(False)

        if shape_changed:
            self.fit_view()
        self._update_cursor_label()

    def set_full_range(self) -> None:
        if self.image is not None and np.issubdtype(self.image.dtype, np.floating):
            finite = self.image[np.isfinite(self.image)]
            lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        else:
            lo, hi = 0.0, self.max_value
        self.auto_levels.setChecked(False)
        self.image_item.setLevels((lo, hi))
        self.hist.setLevels(lo, hi)
        self.hist.setHistogramRange(lo, hi)

    def fit_view(self) -> None:
        if self.image is not None:
            self.view.setRange(xRange=(0, self.image.shape[1]), yRange=(0, self.image.shape[0]),
                               padding=0.02)

    def clear(self) -> None:
        self.image = None
        self.image_item.clear()
        self.sat_item.clear()
        self.cursor_label.setText("")

    # ------------------------------------------------------------ cursor
    def _on_mouse_moved(self, pos) -> None:
        if not self.view.sceneBoundingRect().contains(pos):
            return
        p = self.view.mapSceneToView(pos)
        self._cursor_xy = (p.x(), p.y())
        self.cursor_moved.emit(p.x(), p.y())
        self._update_cursor_label()

    def _on_mouse_clicked(self, event) -> None:
        if event.double():
            self.fit_view()

    def _update_cursor_label(self) -> None:
        if self.image is None or self._cursor_xy is None:
            return
        x, y = int(np.floor(self._cursor_xy[0])), int(np.floor(self._cursor_xy[1]))
        h, w = self.image.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            v = self.image[y, x]
            text = f"{v:.4g}" if np.issubdtype(np.asarray(v).dtype, np.floating) else str(v)
            self.cursor_label.setText(f"x={x}  y={y}  value={text}")
        else:
            self.cursor_label.setText(f"{w} x {h}")
