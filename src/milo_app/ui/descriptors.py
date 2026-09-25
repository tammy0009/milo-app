"""The descriptor sidebar: every field the simulations carry (Bundle / Inputs / Outputs), each
with a check box. Checked fields show on the graph; unchecked ones do not.

Each distinct value of a checked field is its own node ("time_elapsed = 3.78 min"), linked to
the sims that have that value.

Under them, the runs, each with a check box: an unchecked run is left off the graph, and so are its
ghosts. New runs arrive checked.
  Campaigns  one dropdown per campaign, newest first (the one with the latest run on top), with its
             runs inside; its own box shows or hides the whole campaign. Runs in no campaign are
             under "None", at the bottom.
  Sim Feed   every run, newest first, by title and the time it landed.
A run's box in Campaigns and in the Sim Feed is the same switch.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from milo_app.graph import campaign_of
from milo_app.ui import prefs, theme
from milo_app.ui.format import landed, pretty_name, when

SECTIONS = (("bundle", "Bundle"), ("input", "Inputs"), ("output", "Outputs"), ("campaigns", "Campaigns"),
            ("feed", "Sim Feed"))
RUN_SECTIONS = ("campaigns", "feed")  # filled from the runs, not from the fields
NO_CAMPAIGN = "None"
# Checked until you choose your own: the bundle.json fields every run has.
DEFAULT_GROUPS = ["bundle:product", "bundle:module", "bundle:task"]
KEY_ROLE = Qt.ItemDataRole.UserRole + 1
SIM_ROLE = Qt.ItemDataRole.UserRole + 2
CAMPAIGN_ROLE = Qt.ItemDataRole.UserRole + 3  # the campaign's name; "" for the None dropdown
CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable


def campaigns_of(sims: list[dict[str, Any]]) -> list[tuple[str | None, list[dict[str, Any]]]]:
    """[(campaign or None, its runs newest first)], newest campaign first, runs in no campaign last."""
    grouped: dict[str | None, list[dict[str, Any]]] = {}
    for sim in sorted(sims, key=landed, reverse=True):
        grouped.setdefault(campaign_of(sim), []).append(sim)
    return [(c, runs) for c, runs in grouped.items() if c is not None] + (
        [(None, grouped[None])] if None in grouped else [])


class DescriptorPanel(QWidget):
    changed = Signal(list)  # checked group keys, in the order they were checked
    sims_changed = Signal()  # a run (or a whole campaign) was checked or unchecked

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Descriptors")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.store = prefs.store()
        saved = self.store.value("active_groups", DEFAULT_GROUPS)
        self.active: list[str] = list(saved) if isinstance(saved, list) else [saved] if saved else []
        # The runs unchecked (kept as the hidden ones, so a new run shows up checked).
        hidden = self.store.value("hidden_sims", [])
        self.hidden_sims: set[str] = set(hidden) if isinstance(hidden, list) else {hidden} if hidden else set()
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
        section = self.sections["campaigns"]
        opened = {section.child(i).data(0, CAMPAIGN_ROLE) for i in range(section.childCount())
                  if section.child(i).isExpanded()}
        section.takeChildren()
        for campaign, runs in campaigns_of(sims):
            head = QTreeWidgetItem([campaign or NO_CAMPAIGN])
            head.setData(0, CAMPAIGN_ROLE, campaign or "")
            head.setFlags(CHECKABLE)
            head.setToolTip(0, f"{campaign or 'Runs in no campaign'}\n{len(runs)} run{'s' if len(runs) != 1 else ''}")
            section.addChild(head)
            for sim in runs:
                head.addChild(self._run_item(sim))
            head.setExpanded((campaign or "") in opened)
        feed = self.sections["feed"]
        feed.takeChildren()
        for sim in sorted(sims, key=landed, reverse=True):
            feed.addChild(self._run_item(sim))
        self._filling = False
        self._sync()
        self._apply_filter(self.filter.text())

    def color_of(self, key: str) -> str:
        palette = theme.GROUP_COLORS
        return palette[self.active.index(key) % len(palette)]

    # ---- runs

    def _run_item(self, sim: dict[str, Any]) -> QTreeWidgetItem:
        bundle_id = sim["bundle_id"]
        title = str(sim.get("title") or bundle_id)
        item = QTreeWidgetItem([f"{title}\n{when(landed(sim))}"])
        item.setData(0, SIM_ROLE, bundle_id)
        item.setFlags(CHECKABLE)
        item.setToolTip(0, f"{title}\n{bundle_id}")
        return item

    def _run_items(self) -> list[QTreeWidgetItem]:
        """Every run's box, in Campaigns and in the Sim Feed."""
        items = []
        campaigns = self.sections["campaigns"]
        for i in range(campaigns.childCount()):
            head = campaigns.child(i)
            items += [head.child(j) for j in range(head.childCount())]
        feed = self.sections["feed"]
        return items + [feed.child(i) for i in range(feed.childCount())]

    def _sync(self) -> None:
        """Every box shows what is hidden: a run's two boxes agree, and a campaign's box is checked,
        unchecked, or half (some of its runs shown)."""
        self._filling = True
        for item in self._run_items():
            shown = item.data(0, SIM_ROLE) not in self.hidden_sims
            item.setCheckState(0, Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
        campaigns = self.sections["campaigns"]
        for i in range(campaigns.childCount()):
            head = campaigns.child(i)
            shown = sum(head.child(j).data(0, SIM_ROLE) not in self.hidden_sims for j in range(head.childCount()))
            head.setCheckState(0, Qt.CheckState.Checked if shown == head.childCount()
                               else Qt.CheckState.Unchecked if shown == 0 else Qt.CheckState.PartiallyChecked)
        self._filling = False

    def _set_runs_shown(self, bundle_ids: list[str], shown: bool) -> None:
        if shown:
            self.hidden_sims -= set(bundle_ids)
        else:
            self.hidden_sims |= set(bundle_ids)
        self.store.setValue("hidden_sims", sorted(self.hidden_sims))
        self._sync()
        self.sims_changed.emit()

    # ---- clicks

    def _on_item(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._filling:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        bundle_id = item.data(0, SIM_ROLE)
        if bundle_id is not None:
            return self._set_runs_shown([bundle_id], checked)
        if item.data(0, CAMPAIGN_ROLE) is not None:  # a whole campaign
            return self._set_runs_shown([item.child(j).data(0, SIM_ROLE) for j in range(item.childCount())], checked)
        key = item.data(0, KEY_ROLE)
        if key is None:
            return
        if checked and key not in self.active:
            self.active.append(key)
        elif not checked and key in self.active:
            self.active.remove(key)
        self.store.setValue("active_groups", self.active)
        self.changed.emit(list(self.active))

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()

        def matches(item: QTreeWidgetItem) -> bool:
            key = item.data(0, KEY_ROLE) or item.data(0, SIM_ROLE) or ""
            return text in item.text(0).lower() or text in key.lower()

        for section in self.sections.values():
            shown = 0
            for i in range(section.childCount()):
                item = section.child(i)
                if item.data(0, CAMPAIGN_ROLE) is not None:
                    # a campaign: all its runs when its name matches, else just the runs that match
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
