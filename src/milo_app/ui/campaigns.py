"""Campaigns in the side panels (the graph's sidebar and the Ghosts tab): one dropdown per campaign,
newest first (the one with the latest run on top), with its runs inside; the runs in no campaign are
under "None", at the bottom.

Every run has a check box, and so does every campaign (checked, unchecked, or half when only some of
its runs are on). Next to a campaign's name is its color dot: click it to pick the campaign's color.
The color is the campaign's everywhere: both panels, and the ring the graph draws around its runs.

CampaignList runs one such dropdown inside a tree: it fills it, keeps the boxes in step with the set of
runs that are off, and saves that set under its own preferences key.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QColorDialog, QStyle, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem

from milo_app.graph import campaign_of
from milo_app.ui import prefs, theme
from milo_app.ui.format import landed, when

NO_CAMPAIGN = "None"
SIM_ROLE = Qt.ItemDataRole.UserRole + 2
CAMPAIGN_ROLE = Qt.ItemDataRole.UserRole + 3  # the campaign's name; "" for the None dropdown
ALL_ROLE = Qt.ItemDataRole.UserRole + 4  # the "All runs" row (Ghosts tab)
CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
DOT = theme.SIZES.get("campaign_dot", 10)
COLORS_KEY = "campaign_colors"


# ---------------------------------------------------------------------------- colors


def colors() -> dict[str, str]:
    try:
        saved = json.loads(prefs.store().value(COLORS_KEY, "{}") or "{}")
    except (TypeError, ValueError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def color_of(campaign: str) -> str:
    """The color the user picked for a campaign, else one from the palette that stays with its name."""
    picked = colors().get(campaign)
    if picked:
        return picked
    palette = theme.GROUP_COLORS
    return palette[int(hashlib.md5(campaign.encode()).hexdigest(), 16) % len(palette)]


def set_color(campaign: str, color: str) -> None:
    saved = colors()
    saved[campaign] = color
    prefs.store().setValue(COLORS_KEY, json.dumps(saved))


def dot_icon(color: str) -> QIcon:
    size = DOT * 2  # drawn at twice the size so it stays round on high-DPI screens
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------- the runs


def campaigns_of(sims: list[dict[str, Any]]) -> list[tuple[str | None, list[dict[str, Any]]]]:
    """[(campaign or None, its runs newest first)], newest campaign first, runs in no campaign last."""
    grouped: dict[str | None, list[dict[str, Any]]] = {}
    for sim in sorted(sims, key=landed, reverse=True):
        grouped.setdefault(campaign_of(sim), []).append(sim)
    return [(c, runs) for c, runs in grouped.items() if c is not None] + (
        [(None, grouped[None])] if None in grouped else [])


def run_item(sim: dict[str, Any]) -> QTreeWidgetItem:
    """A run: its title and when it landed, with a check box."""
    bundle_id = sim["bundle_id"]
    title = str(sim.get("title") or bundle_id)
    item = QTreeWidgetItem([f"{title}\n{when(landed(sim))}"])
    item.setData(0, SIM_ROLE, bundle_id)
    item.setFlags(CHECKABLE)
    item.setToolTip(0, f"{title}\n{bundle_id}")
    return item


class CampaignList(QObject):
    changed = Signal()  # runs were switched on or off
    recolored = Signal()  # a campaign got a new color

    def __init__(self, tree: QTreeWidget, section: QTreeWidgetItem, store_key: str, isolate: bool = False) -> None:
        """isolate: checking a campaign turns every run outside it off (the Ghosts tab looks inside one
        campaign at a time)."""
        super().__init__(tree)
        self.tree, self.section, self.key, self.isolate = tree, section, store_key, isolate
        self.store = prefs.store()
        saved = self.store.value(store_key, [])
        self.hidden: set[str] = set(saved) if isinstance(saved, list) else {saved} if saved else set()
        self.all_runs: list[str] = []
        self.extra_items: list[QTreeWidgetItem] = []  # other boxes for the same runs (the Sim Feed)
        self.filling = False
        tree.setIconSize(QSize(DOT, DOT))
        tree.viewport().installEventFilter(self)

    # ---- filling

    def fill(self, sims: list[dict[str, Any]]) -> None:
        """Refill the dropdowns, keeping which are open."""
        self.filling = True
        opened = {self.section.child(i).data(0, CAMPAIGN_ROLE) for i in range(self.section.childCount())
                  if self.section.child(i).isExpanded()}
        self.section.takeChildren()
        self.all_runs = [s["bundle_id"] for s in sims]
        if self.isolate:  # one click back to every run, since checking a campaign keeps only its runs
            every = QTreeWidgetItem(["All runs"])
            every.setData(0, ALL_ROLE, True)
            every.setFlags(CHECKABLE)
            every.setToolTip(0, "Use every run")
            self.section.addChild(every)
        for campaign, runs in campaigns_of(sims):
            head = QTreeWidgetItem([campaign or NO_CAMPAIGN])
            head.setData(0, CAMPAIGN_ROLE, campaign or "")
            head.setFlags(CHECKABLE)
            count = f"{len(runs)} run{'s' if len(runs) != 1 else ''}"
            if campaign:
                head.setIcon(0, dot_icon(color_of(campaign)))
                head.setToolTip(0, f"{campaign}\n{count}\nClick the dot to change its color")
            else:
                head.setToolTip(0, f"Runs in no campaign\n{count}")
            self.section.addChild(head)
            for sim in runs:
                head.addChild(run_item(sim))
            head.setExpanded((campaign or "") in opened)
        self.filling = False
        self.sync()

    def heads(self) -> list[QTreeWidgetItem]:
        """The campaign dropdowns (not the All runs row)."""
        return [self.section.child(i) for i in range(self.section.childCount())
                if self.section.child(i).data(0, CAMPAIGN_ROLE) is not None]

    def run_items(self) -> list[QTreeWidgetItem]:
        items = [head.child(j) for head in self.heads() for j in range(head.childCount())]
        return items + self.extra_items

    def shown(self) -> list[str]:
        return [b for b in self.all_runs if b not in self.hidden]

    def campaign_runs(self, campaign: str) -> list[str]:
        """The runs of a campaign ("" for the runs in no campaign)."""
        for head in self.heads():
            if head.data(0, CAMPAIGN_ROLE) == campaign:
                return [head.child(j).data(0, SIM_ROLE) for j in range(head.childCount())]
        return []

    def sync(self) -> None:
        """Every box shows what is off: a run's boxes agree, a campaign's box is checked, unchecked or half."""
        self.filling = True
        for item in self.run_items():
            on = item.data(0, SIM_ROLE) not in self.hidden
            item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        for i in range(self.section.childCount()):
            if self.section.child(i).data(0, ALL_ROLE):
                self.section.child(i).setCheckState(0, Qt.CheckState.Unchecked if self.hidden & set(self.all_runs)
                                                    else Qt.CheckState.Checked)
        for head in self.heads():
            on = sum(head.child(j).data(0, SIM_ROLE) not in self.hidden for j in range(head.childCount()))
            head.setCheckState(0, Qt.CheckState.Checked if on == head.childCount()
                               else Qt.CheckState.Unchecked if on == 0 else Qt.CheckState.PartiallyChecked)
        self.filling = False

    def refresh_colors(self) -> None:
        for head in self.heads():
            campaign = head.data(0, CAMPAIGN_ROLE)
            if campaign:
                head.setIcon(0, dot_icon(color_of(campaign)))

    # ---- clicks

    def handle(self, item: QTreeWidgetItem) -> bool:
        """A box in this list (or one of its extra run boxes) changed. Returns False if it is not ours."""
        if self.filling:
            return True
        checked = item.checkState(0) == Qt.CheckState.Checked
        if item.data(0, ALL_ROLE):
            if checked:
                self.hidden = set()
                self.save()
            else:
                self.sync()  # "All runs" is turned off by choosing a campaign or a run
            return True
        bundle_id = item.data(0, SIM_ROLE)
        if bundle_id is not None and (item in self.extra_items or (
                item.parent() is not None and item.parent().parent() is self.section)):
            self.set_shown([bundle_id], checked)
            return True
        if item.parent() is self.section and item.data(0, CAMPAIGN_ROLE) is not None:
            runs = [item.child(j).data(0, SIM_ROLE) for j in range(item.childCount())]
            if checked and self.isolate:
                self.hidden = set(self.all_runs) - set(runs)  # just this campaign
                self.save()
            else:
                self.set_shown(runs, checked)
            return True
        return False

    def set_shown(self, bundle_ids: list[str], on: bool) -> None:
        if on:
            self.hidden -= set(bundle_ids)
        else:
            self.hidden |= set(bundle_ids)
        self.save()

    def save(self) -> None:
        self.store.setValue(self.key, sorted(self.hidden))
        self.sync()
        self.changed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        """A click on a campaign's color dot opens the color picker."""
        if (event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
                and watched is self.tree.viewport()):
            head = self._dot_at(event.position().toPoint())
            if head is not None:
                campaign = head.data(0, CAMPAIGN_ROLE)
                picked = QColorDialog.getColor(QColor(color_of(campaign)), self.tree.window(), f"Color for {campaign}")
                if picked.isValid():
                    set_color(campaign, picked.name())
                    self.refresh_colors()
                    self.recolored.emit()
                return True
        return False

    def _dot_at(self, point) -> QTreeWidgetItem | None:
        item = self.tree.itemAt(point)
        if item is None or item.parent() is not self.section or not item.data(0, CAMPAIGN_ROLE):
            return None
        # the dot is drawn just right of the check box
        option = QStyleOptionViewItem()
        index = self.tree.indexFromItem(item)
        self.tree.itemDelegate().initStyleOption(option, index)
        option.rect = self.tree.visualRect(index)
        check = self.tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, self.tree)
        start = check.right() + 2
        return item if start <= point.x() <= start + DOT + 8 else None
