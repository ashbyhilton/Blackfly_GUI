"""Widgets bound to camera features by name.

Each widget reads its range / entries / description from the camera via the
:class:`CameraController`, writes through it (so prerequisites and stream
pauses are handled), hides itself when the camera lacks the feature, and
explains in its tooltip why it is locked when it cannot be set.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..camera.base import FeatureInfo
from ..camera.controller import CameraController


def _tooltip(info: FeatureInfo) -> str:
    parts = [f"<b>{info.display_name}</b> <i>({info.name})</i>"]
    if info.description:
        parts.append(info.description)
    if info.min is not None and info.max is not None:
        parts.append(f"Range: {info.min:g} … {info.max:g} {info.unit}".rstrip())
    if info.note:
        parts.append(f"<span style='color:#d08000'>{info.note}</span>")
    return "<br>".join(parts)


class FeatureWidget(QWidget):
    """Base class: subclasses implement :meth:`_update_from` and call
    :meth:`apply` with a new value."""

    def __init__(self, controller: CameraController, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.name = name
        self.info: FeatureInfo | None = None
        self.label: QLabel | None = None  # set by FeatureForm so the row hides together
        self._updating = False

    def refresh(self) -> None:
        self.info = self.controller.describe(self.name)
        present = self.info is not None and self.info.readable
        self.setVisible(present)
        if self.label is not None:
            self.label.setVisible(present)
        if not present:
            return
        assert self.info is not None
        self._updating = True
        try:
            self._update_from(self.info)
        finally:
            self._updating = False
        self.setEnabled(self.info.settable or self._always_enabled())
        tip = _tooltip(self.info)
        self.setToolTip(tip)
        if self.label is not None:
            self.label.setToolTip(tip)

    def _always_enabled(self) -> bool:
        return False

    def _update_from(self, info: FeatureInfo) -> None:
        raise NotImplementedError

    def apply(self, value: Any) -> None:
        if self._updating:
            return
        self.controller.set(self.name, value)


class FeatureCombo(FeatureWidget):
    def __init__(self, controller: CameraController, name: str, parent: QWidget | None = None) -> None:
        super().__init__(controller, name, parent)
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo)
        self.combo.activated.connect(lambda _i: self.apply(self.combo.currentText()))

    def _update_from(self, info: FeatureInfo) -> None:
        entries = list(info.entries)
        if info.value is not None and info.value not in entries:
            entries.append(str(info.value))
        if [self.combo.itemText(i) for i in range(self.combo.count())] != entries:
            self.combo.clear()
            self.combo.addItems(entries)
        self.combo.setCurrentText(str(info.value))


class FeatureCheck(FeatureWidget):
    def __init__(self, controller: CameraController, name: str, text: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(controller, name, parent)
        self.check = QCheckBox(text)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.check)
        self.check.toggled.connect(self.apply)

    def _update_from(self, info: FeatureInfo) -> None:
        self.check.setChecked(bool(info.value))


class FeatureButton(FeatureWidget):
    """Executes a command feature."""

    def __init__(self, controller: CameraController, name: str, text: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(controller, name, parent)
        self.button = QPushButton(text)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.button)
        self.button.clicked.connect(lambda: self.controller.execute(self.name))

    def _update_from(self, info: FeatureInfo) -> None:
        pass


class FeatureReadout(FeatureWidget):
    """Read-only value display."""

    def __init__(self, controller: CameraController, name: str, fmt: str = "{:.3g}",
                 parent: QWidget | None = None) -> None:
        super().__init__(controller, name, parent)
        self.fmt = fmt
        self.value_label = QLabel()
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.value_label)

    def _always_enabled(self) -> bool:
        return True

    def _update_from(self, info: FeatureInfo) -> None:
        v = info.value
        text = self.fmt.format(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
        self.value_label.setText(f"{text} {info.unit}".strip())


class _SpinBox(QDoubleSpinBox):
    """Spin box that only applies on Enter / focus-out (not every keystroke)
    and does not change on accidental mouse-wheel scrolling."""

    def __init__(self) -> None:
        super().__init__()
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCorrectionMode(QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FeatureNumber(FeatureWidget):
    """Numeric feature: spin box, optionally with a (log-scaled) slider."""

    SLIDER_STEPS = 1000

    def __init__(self, controller: CameraController, name: str, decimals: int | None = None,
                 slider: bool = False, log: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(controller, name, parent)
        self.decimals = decimals
        self.log = log
        self.spin = _SpinBox()
        self.spin.setMinimumWidth(110)
        self.spin.valueChanged.connect(self._on_spin)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider: QSlider | None = None
        if slider:
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(0, self.SLIDER_STEPS)
            self.slider.setMinimumWidth(120)
            self.slider.valueChanged.connect(self._on_slider)
            lay.addWidget(self.slider, 1)
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(60)
            self._debounce.timeout.connect(self._apply_slider)
        lay.addWidget(self.spin)

    # value <-> slider position
    def _to_pos(self, v: float) -> int:
        lo, hi = self._range()
        if hi <= lo:
            return 0
        if self.log and lo > 0:
            f = (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
        else:
            f = (v - lo) / (hi - lo)
        return int(round(max(0.0, min(1.0, f)) * self.SLIDER_STEPS))

    def _from_pos(self, pos: int) -> float:
        lo, hi = self._range()
        f = pos / self.SLIDER_STEPS
        if self.log and lo > 0:
            return math.exp(math.log(lo) + f * (math.log(hi) - math.log(lo)))
        return lo + f * (hi - lo)

    def _range(self) -> tuple[float, float]:
        return self.spin.minimum(), self.spin.maximum()

    def _update_from(self, info: FeatureInfo) -> None:
        is_int = info.type == "int"
        decimals = 0 if is_int else (self.decimals if self.decimals is not None else 3)
        self.spin.blockSignals(True)
        self.spin.setDecimals(decimals)
        lo = info.min if info.min is not None else -1e12
        hi = info.max if info.max is not None else 1e12
        self.spin.setRange(lo, hi)
        if info.inc:
            self.spin.setSingleStep(info.inc)
        elif not is_int:
            self.spin.setSingleStep(10 ** (math.floor(math.log10(max(abs(hi - lo), 1e-9))) - 2))
        self.spin.setSuffix(f" {info.unit}" if info.unit else "")
        if not self.spin.hasFocus() and info.value is not None:
            self.spin.setValue(float(info.value))
        self.spin.blockSignals(False)
        if self.slider is not None and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(self._to_pos(float(info.value or 0)))
            self.slider.blockSignals(False)

    def _on_spin(self, value: float) -> None:
        if self.info is not None and self.info.type == "int" and self.info.inc and self.info.inc > 1:
            lo = self.info.min or 0
            value = lo + round((value - lo) / self.info.inc) * self.info.inc
        self.apply(int(round(value)) if self.info and self.info.type == "int" else value)

    def _on_slider(self, _pos: int) -> None:
        if self._updating:
            return
        self.spin.blockSignals(True)
        self.spin.setValue(self._from_pos(self.slider.value()))  # type: ignore[union-attr]
        self.spin.blockSignals(False)
        self._debounce.start()

    def _apply_slider(self) -> None:
        self._on_spin(self.spin.value())


class FeatureForm(QGroupBox):
    """A titled form of feature widgets. Rows hide when the camera lacks a
    feature; the whole group hides when it has none of them."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.widgets: list[FeatureWidget] = []

    def add(self, label: str, widget: FeatureWidget) -> FeatureWidget:
        lbl = QLabel(label)
        widget.label = lbl
        self.form.addRow(lbl, widget)
        self.widgets.append(widget)
        return widget

    def add_row(self, label: str | QWidget, widget: QWidget | None = None) -> None:
        """Add a non-feature row (buttons, hints). A single widget spans the row."""
        if widget is None:
            self.form.addRow(label)
        else:
            self.form.addRow(label, widget)

    def refresh(self) -> None:
        for w in self.widgets:
            w.refresh()
        self.setVisible(not self.widgets or any(not w.isHidden() for w in self.widgets))


class FeaturePanel(QWidget):
    """Scrollable column of :class:`FeatureForm` groups that refreshes when
    the camera state changes (coalesced) and periodically (to follow auto
    exposure/gain)."""

    refreshed = Signal()

    def __init__(self, controller: CameraController, parent: QWidget | None = None,
                 poll_ms: int = 1000) -> None:
        super().__init__(parent)
        self.controller = controller
        self.forms: list[FeatureForm] = []
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(6, 6, 6, 6)
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self.refresh)
        controller.features_changed.connect(self._pending.start)
        controller.closed.connect(self._pending.start)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_refresh)
        if poll_ms:
            self._poll.start(poll_ms)

    def add_form(self, form: FeatureForm) -> FeatureForm:
        self.forms.append(form)
        self.layout_.addWidget(form)
        return form

    def finish(self) -> None:
        self.layout_.addStretch(1)
        self.refresh()

    def _poll_refresh(self) -> None:
        if self.isVisible() and self.controller.is_open:
            self.refresh()

    def refresh(self) -> None:
        open_ = self.controller.is_open
        self.setEnabled(open_)
        for f in self.forms:
            if open_:
                f.refresh()
        self.refreshed.emit()
