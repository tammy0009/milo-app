"""The descriptor sidebar: every field the simulations carry (Bundle / Inputs / Outputs), each
with a check box. Checked fields show on the graph; unchecked ones do not.

Each distinct value of a checked field is its own node ("time_elapsed = 3.78 min"), linked to
the sims that have that value.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from milo_app.ui import prefs, theme
from milo_app.ui.format import pretty_name

SECTIONS = (("bundle", "Bundle"), ("input", "Inputs"), ("output", "Outputs"))
# Checked until you choose your own: the bundle.json fields every run has.
DEFAULT_GROUPS = ["bundle:product", "bundle:module", "bundle:task"]
KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class DescriptorPanel(QWidget):
    changed = Signal(list)  # checked group keys, in the order they were checked

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Descriptors")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.store = prefs.store()
        saved = self.store.value("active_groups", DEFAULT_GROUPS)
        self.active: list[str] = list(saved) if isinstance(saved, list) else [saved] if saved else []
        self._filling = False

        title = QLabel("DESCRIPTORS", objectName="PanelTitle")
        self.filter = QLineEdit(placeholderText="Filter")
        self.filter.textChanged.connect(self._apply_filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.itemChanged.connect(self._on_item)
        self.sections: dict[str, QTreeWidgetItem] = {}
        for source, label in SECTIONS:
            item = QTreeWidgetItem([label])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setFont(0, theme.font(theme.SIZES["small"], bold=True))
            self.tree.addTopLevelItem(item)
            item.setExpanded(source == "bundle")
            self.sections[source] = item

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 14, 8, 10)
        box.setSpacing(8)
        box.addWidget(title)
        box.addWidget(self.filter)
        box.addWidget(self.tree, 1)

    def set_groups(self, groups: list[dict[str, Any]]) -> None:
        """Refill the list from the graph, keeping what is checked."""
        self._filling = True
        for section in self.sections.values():
            section.takeChildren()
        for group in groups:
            item = QTreeWidgetItem([pretty_name(group["name"])])
            item.setData(0, KEY_ROLE, group["key"])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if group["key"] in self.active else Qt.CheckState.Unchecked)
            item.setToolTip(0, group["key"])
            self.sections[group["source"]].addChild(item)
        self._filling = False
        self._apply_filter(self.filter.text())

    def color_of(self, key: str) -> str:
        palette = theme.GROUP_COLORS
        return palette[self.active.index(key) % len(palette)]

    def _on_item(self, item: QTreeWidgetItem, _column: int) -> None:
        key = item.data(0, KEY_ROLE)
        if self._filling or key is None:
            return
        on = item.checkState(0) == Qt.CheckState.Checked
        if on and key not in self.active:
            self.active.append(key)
        elif not on and key in self.active:
            self.active.remove(key)
        self.store.setValue("active_groups", self.active)
        self.changed.emit(list(self.active))

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for section in self.sections.values():
            shown = 0
            for i in range(section.childCount()):
                item = section.child(i)
                hide = bool(text) and text not in item.text(0).lower() and text not in item.data(0, KEY_ROLE).lower()
                item.setHidden(hide)
                shown += not hide
            if text:
                section.setExpanded(shown > 0)
