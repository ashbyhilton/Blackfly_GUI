"""'All features' browser: every feature the camera exposes, searchable and
editable. The escape hatch for anything without a dedicated widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...camera.controller import CameraController
from ..widgets import (
    FeatureButton,
    FeatureCheck,
    FeatureCombo,
    FeatureNumber,
    FeatureReadout,
    FeatureWidget,
)

ROLE_NAME = Qt.ItemDataRole.UserRole


def make_widget(controller: CameraController, name: str, kind: str, settable: bool) -> FeatureWidget:
    if kind == "enum":
        return FeatureCombo(controller, name)
    if kind == "bool":
        return FeatureCheck(controller, name, name)
    if kind in ("int", "float") and settable:
        return FeatureNumber(controller, name, decimals=4)
    if kind == "command":
        return FeatureButton(controller, name, f"Execute {name}")
    return FeatureReadout(controller, name, "{}")


class FeaturesPanel(QWidget):
    def __init__(self, controller: CameraController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search features (name or description)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Feature", "Value"])
        self.tree.setColumnWidth(0, 230)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemExpanded.connect(lambda _item: self.refresh_values())

        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_title = QLabel("Select a feature")
        self.detail_title.setWordWrap(True)
        self.detail_desc = QLabel("")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_layout.addWidget(self.detail_title)
        self.detail_layout.addWidget(self.detail_desc)
        self.editor: FeatureWidget | None = None
        self.detail_layout.addStretch(1)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.search)
        lay.addWidget(split, 1)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._kinds: dict[str, str] = {}
        self._descriptions: dict[str, str] = {}
        controller.opened.connect(lambda _dev: self.rebuild())
        controller.closed.connect(self.rebuild)
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(100)
        self._pending.timeout.connect(self.refresh_values)
        controller.features_changed.connect(self._pending.start)
        self._poll = QTimer(self)
        self._poll.timeout.connect(lambda: self.isVisible() and self.refresh_values())
        self._poll.start(2000)

    def rebuild(self) -> None:
        self.tree.clear()
        self._items.clear()
        self._set_editor(None)
        c = self.controller
        if not c.is_open:
            return
        assert c.backend is not None
        for cat, names in c.backend.feature_tree():
            parent = QTreeWidgetItem([cat])
            parent.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(parent)
            for name in names:
                info = c.describe(name)
                if info is None:
                    continue
                item = QTreeWidgetItem([info.display_name, ""])
                item.setData(0, ROLE_NAME, name)
                item.setToolTip(0, name)
                parent.addChild(item)
                self._items[name] = item
                self._kinds[name] = info.type
                self._descriptions[name] = info.description
        self.refresh_values()
        self._filter(self.search.text())

    def refresh_values(self) -> None:
        if not self.isVisible() or not self.controller.is_open:
            return
        for name, item in self._items.items():
            if item.isHidden() or (item.parent() is not None and not item.parent().isExpanded()):
                continue
            info = self.controller.describe(name)
            if info is None:
                continue
            if info.type == "command":
                text = "(command)"
            elif isinstance(info.value, float):
                text = f"{info.value:.6g} {info.unit}".strip()
            else:
                text = f"{info.value} {info.unit}".strip()
            item.setText(1, text)
            item.setForeground(1, self.palette().text() if info.settable else self.palette().placeholderText())
        if self.editor is not None:
            self.editor.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.refresh_values()

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(parent.childCount()):
                item = parent.child(j)
                name = item.data(0, ROLE_NAME)
                hit = not text or text in name.lower() or text in item.text(0).lower() \
                    or text in self._descriptions.get(name, "").lower()
                item.setHidden(not hit)
                any_visible |= hit
            parent.setHidden(not any_visible)
            parent.setExpanded(bool(text) and any_visible)
        if text:
            self.refresh_values()

    def _on_select(self, item: QTreeWidgetItem | None, _prev) -> None:
        name = item.data(0, ROLE_NAME) if item is not None else None
        if not name:
            self._set_editor(None)
            return
        info = self.controller.describe(name)
        if info is None:
            self._set_editor(None)
            return
        self.detail_title.setText(f"<b>{info.display_name}</b>  <code>{name}</code>  <i>({info.type})</i>")
        desc = info.description
        if info.note:
            desc += f"<br><span style='color:#d08000'>{info.note}</span>"
        self.detail_desc.setText(desc)
        self._set_editor(make_widget(self.controller, name, info.type, info.settable))

    def _set_editor(self, widget: FeatureWidget | None) -> None:
        if self.editor is not None:
            self.editor.setParent(None)
            self.editor.deleteLater()
        self.editor = widget
        if widget is None:
            self.detail_title.setText("Select a feature")
            self.detail_desc.setText("")
            return
        self.detail_layout.insertWidget(2, widget)
        widget.refresh()
