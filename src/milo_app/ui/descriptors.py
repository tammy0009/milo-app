"""The descriptor sidebar: every field the simulations carry (Bundle / Inputs / Outputs), each
with a check box. Checked fields show on the graph; unchecked ones do not.

Each distinct value of a checked field is its own node ("time_elapsed = 3.78 min"), linked to
the sims that have that value.

Under them, the runs, each with a check box: an unchecked run is left off the graph, and so are its
ghosts. New runs arrive checked.
  Campaigns  one dropdown per campaign with its runs, and its color dot (campaigns.py): the graph
             draws a ring of that color around a campaign's runs.
  Sim Feed   every run, newest first, by title and the time it landed.
A run's box in Campaigns and in the Sim Feed is the same switch.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from milo_app.ui import prefs, theme
from milo_app.ui.campaigns import CAMPAIGN_ROLE, CHECKABLE, SIM_ROLE, CampaignList, run_item
from milo_app.ui.format import landed, pretty_name

SECTIONS = (("bundle", "Bundle"), ("input", "Inputs"), ("output", "Outputs"), ("campaigns", "Campaigns"),
            ("feed", "Sim Feed"))
RUN_SECTIONS = ("campaigns", "feed")  # filled from the runs, not from the fields
# Checked until you choose your own: the bundle.json fields every run has.
DEFAULT_GROUPS = ["bundle:product", "bundle:module", "bundle:task"]
KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class DescriptorPanel(QWidget):
    changed = Signal(list)  # checked group keys, in the order they were checked
    sims_changed = Signal()  # a run (or a whole campaign) was checked or unchecked
    recolored = Signal()  # a campaign got a new color

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
        # the runs unchecked (kept as the hidden ones, so a new run shows up checked)
        self.campaigns = CampaignList(self.tree, self.sections["campaigns"], "hidden_sims")
        self.campaigns.changed.connect(self.sims_changed)
        self.campaigns.recolored.connect(self.recolored)

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 14, 8, 10)
        box.setSpacing(8)
        box.addWidget(title)
        box.addWidget(self.filter)
        box.addWidget(self.tree, 1)

    @property
    def hidden_sims(self) -> set[str]:
        return self.campaigns.hidden

    def set_groups(self, groups: list[dict[str, Any]]) -> None:
        """Refill the list from the graph, keeping what is checked."""
        self._filling = True
        for source, section in self.sections.items():
            if source not in RUN_SECTIONS:
                section.takeChildren()
        for group in groups:
            item = QTreeWidgetItem([pretty_name(group["name"])])
            item.setData(0, KEY_ROLE, group["key"])
            item.setFlags(CHECKABLE)
            item.setCheckState(0, Qt.CheckState.Checked if group["key"] in self.active else Qt.CheckState.Unchecked)
            item.setToolTip(0, group["key"])
            self.sections[group["source"]].addChild(item)
        self._filling = False
        self._apply_filter(self.filter.text())

    def set_sims(self, sims: list[dict[str, Any]]) -> None:
        """Refill Campaigns and the Sim Feed, keeping which campaigns are open."""
        self._filling = True
        feed = self.sections["feed"]
        feed.takeChildren()
        items = [run_item(sim) for sim in sorted(sims, key=landed, reverse=True)]
        for item in items:
            feed.addChild(item)
        self.campaigns.extra_items = items
        self._filling = False
        self.campaigns.fill(sims)
        self._apply_filter(self.filter.text())

    def refresh_colors(self) -> None:
        self.campaigns.refresh_colors()

    def color_of(self, key: str) -> str:
        palette = theme.GROUP_COLORS
        return palette[self.active.index(key) % len(palette)]

    def _on_item(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._filling or self.campaigns.handle(item):
            return
        key = item.data(0, KEY_ROLE)
        if key is None:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        if checked and key not in self.active:
            self.active.append(key)
        elif not checked and key in self.active:
            self.active.remove(key)
        self.store.setValue("active_groups", self.active)
        self.changed.emit(list(self.active))

    def _apply_filter(self, text: str) -> None:
        filter_tree(self.sections.values(), text, KEY_ROLE)


def filter_tree(sections, text: str, key_role) -> None:
    """Hide what does not match `text`. A campaign shows all its runs when its name matches, else just
    the runs that match."""
    text = text.strip().lower()

    def matches(item: QTreeWidgetItem) -> bool:
        key = item.data(0, key_role) or item.data(0, SIM_ROLE) or ""
        return text in item.text(0).lower() or text in key.lower()

    for section in sections:
        shown = 0
        for i in range(section.childCount()):
            item = section.child(i)
            if item.data(0, CAMPAIGN_ROLE) is not None:
                inner = 0
                for j in range(item.childCount()):
                    run = item.child(j)
                    hide_run = bool(text) and not matches(item) and not matches(run)
                    run.setHidden(hide_run)
                    inner += not hide_run
                hide = bool(text) and inner == 0
                if text:
                    item.setExpanded(inner > 0)
            else:
                hide = bool(text) and not matches(item)
            item.setHidden(hide)
            shown += not hide
        if text:
            section.setExpanded(shown > 0)
