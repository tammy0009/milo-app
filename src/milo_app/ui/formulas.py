"""The Ghosts tab: pick two or more descriptors and see the formula the model fits between them.

The last one checked is what gets solved for (change it with "Solve for"); the others explain it,
typeset like LaTeX (latex.py; "Copy LaTeX" puts the source on the clipboard):

    NPT Density = 0.9458 − 0.00000555 × Amorphous Cell Cell Volume

Each number carries its ± uncertainty, next to how well the line fits, how sure the relationship is
(from the relationship nodes), and, with one explaining descriptor, a chart of the runs, the line and
its 90 % band. A small calculator turns values of the explaining descriptors into a guess.

Campaigns, at the top of the side panel, are the same dropdowns as on the graph (campaigns.py), and
say which runs this page uses. Checking a campaign keeps just its runs (a run can still be switched on
or off on its own). The stored math is over every run and stays as it is; with only some runs on, it
is worked out again over just those, so a campaign can be looked at on its own.
The formula is predict.formula(): the same Bayesian linear regression as the models (ghost.md 4.1).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSplitter,
    QSizePolicy, QStyleOptionViewItem, QToolTip, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QVBoxLayout, QWidget,
)

from milo_app import predict
from milo_app.graph import campaign_of
from milo_app.ui.campaigns import CampaignList
from milo_app.ui import latex, prefs, theme
from milo_app.ui.format import pretty_name, prior_text, sig, units_text

KEY_ROLE = Qt.ItemDataRole.UserRole + 1
SECTIONS = (("bundle", "Bundle"), ("input", "Inputs"), ("output", "Outputs"))
C = theme.CHART


def as_sim(row: dict[str, Any]) -> dict[str, Any]:
    """A run of graph.descriptor_table() in the shape the campaign dropdowns read."""
    field = {k: row["values"].get(f"bundle:{k}", (None,))[0] for k in ("campaign", "finish", "start")}
    return {"bundle_id": row["id"], "title": row.get("title")} | field


def name(key: str) -> str:
    return pretty_name(key.partition(":")[2])


def number(value: float, digits: int = 4) -> str:
    return sig(value, digits)


def signed(value: float) -> str:
    """"+ 2.3" / "− 2620" for writing terms of a formula."""
    return f"{'−' if value < 0 else '+'} {number(abs(value))}"


class FitChart(QWidget):
    """The runs as dots, the fitted line, and its 90 % band. Hover a dot for its run."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(C["height"])
        self.setMouseTracking(True)
        self.fit: dict[str, Any] | None = None
        self.titles: dict[str, str] = {}
        self._dots: list[tuple[QPointF, str]] = []

    def show_fit(self, fit: dict[str, Any] | None, titles: dict[str, str]) -> None:
        self.fit, self.titles = fit, titles
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        self._dots = []
        fit = self.fit
        if not fit:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        key = fit["predictors"][0]
        xs = np.array(fit["inputs"][key])
        ys = np.array(fit["observed"])
        grid_x = np.linspace(xs.min(), xs.max(), 60)
        band = [predict.guess(fit, {key: float(v)}) for v in grid_x]
        low = np.array([b["low"] for b in band])
        high = np.array([b["high"] for b in band])
        mid = np.array([b["guess"] for b in band])
        x0, x1 = xs.min(), xs.max()
        y0, y1 = min(ys.min(), low.min()), max(ys.max(), high.max())
        padx, pady = (x1 - x0) * 0.06 or 1.0, (y1 - y0) * 0.08 or 1.0
        x0, x1, y0, y1 = x0 - padx, x1 + padx, y0 - pady, y1 + pady
        area = QRectF(C["left"], C["top"], self.width() - C["left"] - C["right"], self.height() - C["top"] - C["bottom"])

        def at(x: float, y: float) -> QPointF:
            return QPointF(area.left() + (x - x0) / (x1 - x0) * area.width(),
                           area.bottom() - (y - y0) / (y1 - y0) * area.height())

        small = theme.font(theme.SIZES["small"])
        p.setFont(small)
        for i in range(5):  # recessive grid and tick labels
            fy = y0 + (y1 - y0) * i / 4
            fx = x0 + (x1 - x0) * i / 4
            p.setPen(QPen(QColor(C["grid"]), 1))
            p.drawLine(at(x0, fy), at(x1, fy))
            p.setPen(QColor(theme.COLORS["muted"]))
            p.drawText(QRectF(0, at(x0, fy).y() - 8, C["left"] - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number(fy, 3))
            p.drawText(QRectF(at(fx, y0).x() - 40, area.bottom() + 4, 80, 16), Qt.AlignmentFlag.AlignHCenter, number(fx, 3))
        p.drawText(QRectF(area.left(), area.bottom() + 20, area.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                   name(key) + (f" ({units_text(fit['predictor_units'][key])})" if fit["predictor_units"].get(key) else ""))

        band_shape = QPolygonF([at(x, y) for x, y in zip(grid_x, high)] + [at(x, y) for x, y in zip(grid_x[::-1], low[::-1])])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["band"]))
        p.drawPolygon(band_shape)
        line = QPainterPath(at(grid_x[0], mid[0]))
        for x, y in zip(grid_x[1:], mid[1:]):
            line.lineTo(at(x, y))
        p.setPen(QPen(QColor(C["line"]), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line)
        for sim, x, y in zip(fit["sims"], xs, ys):
            point = at(x, y)
            p.setPen(QPen(QColor(theme.COLORS["ground"]), 2))  # a 2 px ring keeps overlapping dots apart
            p.setBrush(QColor(C["point"]))
            p.drawEllipse(point, C["dot"] / 2, C["dot"] / 2)
            self._dots.append((point, f"{self.titles.get(sim, sim)}\n{name(key)} {number(x, 6)}\n"
                                      f"{name(fit['target'])} {number(y, 6)}"))
        p.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        near = [(math.hypot(d.x() - pos.x(), d.y() - pos.y()), d, text) for d, text in self._dots]
        hit = min(near, default=None, key=lambda h: h[0])
        if hit and hit[0] <= 10:  # the hit area is bigger than the dot
            # runs that landed on the same spot are listed together
            same = [text for _dist, d, text in near if math.hypot(d.x() - hit[1].x(), d.y() - hit[1].y()) < 2]
            QToolTip.showText(event.globalPosition().toPoint(), "\n\n".join(same), self)
        else:
            QToolTip.hideText()


class FormulaView(QWidget):
    recolored = Signal()  # a campaign got a new color here

    def __init__(self) -> None:
        super().__init__()
        self.all_table: list[dict[str, Any]] = []  # every run
        self.all_relationships: list[dict[str, Any]] = []  # the stored ones, over every run
        self.table: list[dict[str, Any]] = []  # the runs switched on in Campaigns
        self.relationships: list[dict[str, Any]] = []
        self.tests: list[dict[str, Any]] = []
        self.titles: dict[str, str] = {}
        self.checked: list[str] = []
        self.fit: dict[str, Any] | None = None
        self._filling = False

        # left: the descriptors that can go in a formula
        side = QWidget(objectName="Descriptors")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.filter = QLineEdit(placeholderText="Search descriptors")
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
            item.setExpanded(source == "output")
            self.sections[source] = item
        # which runs the math uses: the same Campaigns dropdowns as the graph's, at the very top
        self.runs_tree = QTreeWidget()
        self.runs_tree.setHeaderHidden(True)
        self.runs_tree.setIndentation(12)
        self.runs_tree.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.runs_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # no focus frame round the rows
        section = QTreeWidgetItem(["Campaigns"])
        section.setFlags(Qt.ItemFlag.ItemIsEnabled)
        section.setFont(0, theme.font(theme.SIZES["small"], bold=True))
        self.runs_tree.addTopLevelItem(section)
        section.setExpanded(True)
        self.campaigns = CampaignList(self.runs_tree, section, "formula_hidden_runs", isolate=True)
        self.campaigns.changed.connect(self._apply_scope)
        self.campaigns.recolored.connect(self.recolored)
        self.runs_tree.itemChanged.connect(lambda item, _c: self.campaigns.handle(item))
        self.runs_tree.itemExpanded.connect(lambda _i: self._fit_runs_tree())
        self.runs_tree.itemCollapsed.connect(lambda _i: self._fit_runs_tree())
        box = QVBoxLayout(side)
        box.setContentsMargins(14, 14, 8, 10)
        box.setSpacing(8)
        box.addWidget(self.runs_tree)
        box.addSpacing(6)
        box.addWidget(QLabel("DESCRIPTORS WITH NUMBERS", objectName="PanelTitle"))
        box.addWidget(self.filter)
        box.addWidget(self.tree, 1)

        # right: the formula
        self.hint = QLabel(objectName="Empty", wordWrap=True)
        self.solve = QComboBox()
        self.solve.currentIndexChanged.connect(lambda _i: self._recompute())
        solve_row = QHBoxLayout()
        solve_row.addWidget(QLabel("Solve for", objectName="FieldName"))
        solve_row.addWidget(self.solve, 1)
        self.formula = QLabel(objectName="Formula")
        self.store = prefs.store()
        self.symbolic = QPushButton("Symbols", objectName="Chip", checkable=True)
        self.symbolic.setToolTip("Write every descriptor as a symbol, with a legend")
        self.symbolic.setChecked(self.store.value("formula_symbols", False) in (True, "true"))
        self.symbolic.toggled.connect(self._toggle_symbols)
        self.copy = QPushButton("Copy LaTeX")
        self.copy.clicked.connect(self._copy_latex)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.symbolic)
        buttons.addWidget(self.copy)
        self.legend = QGridLayout()  # symbol | name | units, shown in symbol mode
        self.legend.setHorizontalSpacing(12)
        self.legend.setVerticalSpacing(0)
        legend_row = QHBoxLayout()  # centred under the formula
        legend_row.addStretch()
        legend_row.addLayout(self.legend)
        legend_row.addStretch()
        formula_row = QVBoxLayout()
        formula_row.setSpacing(10)
        formula_row.addLayout(buttons)
        formula_row.addWidget(self.formula)
        formula_row.addLayout(legend_row)
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(14)
        self.facts.setVerticalSpacing(6)
        self.facts.setColumnStretch(1, 1)
        self.chart = FitChart()
        self.calc_title = QLabel("CALCULATOR", objectName="PanelTitle")
        self.calc_inputs = QGridLayout()
        self.calc_inputs.setHorizontalSpacing(10)
        self.calc_inputs.setColumnStretch(2, 1)  # keep each box next to its name
        self.calc_result = QLabel(wordWrap=True)
        self.calc_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._calc_fields: dict[str, QLineEdit] = {}

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addLayout(solve_row)
        body.addLayout(formula_row)
        body.addLayout(self.facts)
        body.addWidget(self.chart)
        body.addWidget(self.calc_title)
        body.addLayout(self.calc_inputs)
        body.addWidget(self.calc_result)
        body.addStretch()

        page = QWidget(objectName="Detail")
        column = QVBoxLayout(page)
        column.setContentsMargins(28, 22, 28, 22)
        column.setSpacing(12)
        self.track = QLabel(objectName="Track", wordWrap=True)
        self.track.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(QLabel("TRACK RECORD", objectName="KindLabel"))
        column.addWidget(self.track)
        column.addSpacing(10)
        column.addWidget(QLabel("FORMULA", objectName="KindLabel"))
        column.addWidget(self.hint)
        column.addWidget(self.body)
        column.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(side)
        split.addWidget(scroll)
        split.setSizes([300, 1100])
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        self._show()

    # ---- data

    def set_data(self, table: list[dict[str, Any]], relationships: list[dict[str, Any]], titles: dict[str, str]) -> None:
        """Called whenever the graph changes; keeps which runs are on and what is checked."""
        self.all_table, self.all_relationships, self.titles = table, relationships, titles
        self.campaigns.fill([as_sim(row) for row in table])
        self._fit_runs_tree()
        self._apply_scope()

    def refresh_colors(self) -> None:
        self.campaigns.refresh_colors()

    def _fit_runs_tree(self) -> None:
        """The Campaigns list is as tall as what is open in it, up to half the panel."""
        tree, total = self.runs_tree, 4
        it = QTreeWidgetItemIterator(tree)
        while it.value():
            item, parent = it.value(), it.value().parent()
            visible = not item.isHidden()
            while visible and parent is not None:
                visible = parent.isExpanded()
                parent = parent.parent()
            if visible:
                option = QStyleOptionViewItem()
                index = tree.indexFromItem(item)
                tree.itemDelegate().initStyleOption(option, index)
                total += tree.itemDelegate().sizeHint(option, index).height() + 6  # the stylesheet's padding
            it += 1
        room = max(120, (self.height() or 800) // 2)
        tree.setFixedHeight(min(total, room))

    def _apply_scope(self) -> None:
        """Use only the runs switched on in Campaigns for everything on this page: the descriptors listed,
        the formula and its chart, the relationships (worked out again over just those runs when not all
        are on), and the track record."""
        on = set(self.campaigns.shown())
        if on == {row["id"] for row in self.all_table}:
            self.table, self.relationships = self.all_table, self.all_relationships
        else:
            self.table = [row for row in self.all_table if row["id"] in on]
            self.relationships = predict.relationships_in(self.table)
        catalog = predict.formula_catalog(self.table)
        self.checked = [k for k in self.checked if any(e["key"] == k for e in catalog)]
        self._filling = True
        for section in self.sections.values():
            section.takeChildren()
        for entry in catalog:
            item = QTreeWidgetItem([name(entry["key"])])
            item.setData(0, KEY_ROLE, entry["key"])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if entry["key"] in self.checked else Qt.CheckState.Unchecked)
            also = [name(a) for a in entry["aliases"] if a != entry["key"]]
            item.setToolTip(0, entry["key"] + (f"\nsame as: {', '.join(also)}" if also else ""))
            self.sections[entry["key"].partition(":")[0]].addChild(item)
        self._filling = False
        self._apply_filter(self.filter.text())
        self._show_track()
        self._recompute()

    def _scope_words(self) -> str:
        """How the runs switched on read in a sentence: "" (every run), " in PLA-PCL water uptake",
        " in no campaign", or " among the 3 runs switched on"."""
        on = {row["id"] for row in self.table}
        if on == {row["id"] for row in self.all_table}:
            return ""
        for row in self.all_table:
            campaign = campaign_of({"campaign": row["values"].get("bundle:campaign", (None,))[0]}) or ""
            if on == set(self.campaigns.campaign_runs(campaign)):
                return f" in {campaign}" if campaign else " in no campaign"
        return f" among the {len(on)} run{'s' if len(on) != 1 else ''} switched on"

    def set_track(self, tests: list[dict[str, Any]]) -> None:
        self.tests = tests
        self._show_track()

    def _show_track(self) -> None:
        """How the blind predictions of real runs have gone so far (blind.py; ghost.md 7.3), for the
        runs in the chosen campaign."""
        ids = {s["id"] for s in self.table}
        tests = [t for t in self.tests if t.get("bundle_id") in ids]
        done = [t for t in tests if t.get("testable")]
        where = self._scope_words()
        if not done and where:
            self.track.setText(f"No run{where} has been blind-tested yet.")
            return
        if not done:
            untestable = len(tests) - len(done)
            self.track.setText("No blind tests yet. The next run that arrives is predicted before it is added, "
                               "then checked against what it really produced."
                               + (f"\n({untestable} run{'s' if untestable != 1 else ''} could not be tested: "
                                  "the first of their kind.)" if untestable else ""))
            return
        values = sum(t.get("values") or 0 for t in done)
        right = sum(t.get("right") or 0 for t in done)
        ranged = sum(t.get("ranged") or 0 for t in done)
        inside = sum(t.get("inside") or 0 for t in done)
        stated = [t["stated"] * t["values"] for t in done if isinstance(t.get("stated"), (int, float)) and t.get("values")]
        expected = sum(stated) / values if values and stated else None
        text = (f"{len(done)} run{'s' if len(done) != 1 else ''}{where} predicted blind before arriving.\n"
                f"Values right (within 5 %): {right} of {values} ({right / values:.0%})"
                + (f"; confidence said {expected:.0%}" if expected is not None else "") + ".")
        if ranged:
            text += f"\nNumbers inside their 90 % ranges: {inside} of {ranged} ({inside / ranged:.0%}; should be about 90 %)."
        self.track.setText(text)

    def _on_item(self, item: QTreeWidgetItem, _column: int) -> None:
        key = item.data(0, KEY_ROLE)
        if self._filling or key is None:
            return
        if item.checkState(0) == Qt.CheckState.Checked and key not in self.checked:
            self.checked.append(key)
        elif item.checkState(0) != Qt.CheckState.Checked and key in self.checked:
            self.checked.remove(key)
        self._recompute(solve_for=self.checked[-1] if self.checked else None)

    def _recompute(self, solve_for: str | None = None) -> None:
        if self._filling:
            return
        current = solve_for or self.solve.currentData()
        self.solve.blockSignals(True)
        self.solve.clear()
        for key in self.checked:
            self.solve.addItem(name(key), key)
        if current in self.checked:
            self.solve.setCurrentIndex(self.checked.index(current))
        self.solve.blockSignals(False)
        target = self.solve.currentData()
        if len(self.table) < 2:
            self.fit = None
            runs = f"{len(self.table)} run" + ("s" if len(self.table) != 1 else "")
            return self._show(f"Only {runs}{self._scope_words()} so far: a formula needs at least 2 runs.")
        if len(self.checked) < 2 or target is None:
            self.fit = None
            return self._show("Check two or more descriptors on the left. The last one checked is solved for; "
                              "the others explain it (change it with Solve for).")
        predictors = [k for k in self.checked if k != target]
        fit = predict.formula(self.table, target, predictors)
        if "error" in fit:
            self.fit = None
            return self._show(fit["error"].capitalize() + ".")
        self.fit = fit
        self._show()

    # ---- showing

    def _show(self, hint: str = "") -> None:
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        self.body.setVisible(self.fit is not None or len(self.checked) >= 2)
        for layout in (self.facts, self.calc_inputs):
            while layout.count():
                widget = layout.takeAt(0).widget()
                if widget:
                    widget.deleteLater()
        self._calc_fields = {}
        fit = self.fit
        if fit is None:
            self.formula.clear()
            self._fill_legend()
            self.copy.hide()
            self.symbolic.hide()
            self.chart.show_fit(None, self.titles)
            self.chart.hide()
            self.calc_title.hide()
            self.calc_result.setText("")
            return
        units = f" {units_text(fit['units'])}" if fit.get("units") else ""
        terms = " ".join(f"{signed(s)} × {name(k)}" for k, s in zip(fit["predictors"], fit["slopes"]))
        plain = f"{name(fit['target'])} = {number(fit['intercept'])} {terms}{units}"
        self.formula.setToolTip(plain)
        self._draw_formula()
        self.copy.show()
        self.symbolic.show()

        rows: list[tuple[str, str]] = []
        for k, s, e in zip(fit["predictors"], fit["slopes"], fit["slope_sd"]):
            pu = units_text(fit["predictor_units"].get(k))
            per = f" per {pu}" if pu else " per unit"
            rows.append((f"× {name(k)}", f"{number(s)} ± {number(e, 3)}{units}{per}"))
            rows.append(("   relationship", self._relationship_text(fit["target"], k)))
        for k in fit.get("left_out") or []:
            rows.append((f"× {name(k)}", "left out: the same in every one of these runs"))
        rows += [
            ("Fit", f"R² = {fit['r2']:.3f}: the line explains {max(fit['r2'], 0):.0%} of how "
                    f"{name(fit['target'])} varies across these runs" if fit.get("r2") is not None else "n/a"),
            ("Runs used", f"{fit['n']} (every run{self._scope_words()} that has all of these)"),
            ("Scatter", f"± {number(fit['noise_sd'], 3)}{units} run to run (estimated)" if fit.get("noise_sd") else "n/a"),
            ("Method", "Bayesian linear regression (ghost.md 4.1). ± is each slope's own uncertainty;\n"
                       "the line is fitted anew whenever runs arrive."),
            ("Prior", prior_text(fit.get("prior"))),
        ]
        for i, (label, text) in enumerate(rows):
            value = QLabel(text, wordWrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.facts.addWidget(QLabel(label, objectName="FieldName"), i, 0, Qt.AlignmentFlag.AlignTop)
            self.facts.addWidget(value, i, 1)

        one = len(fit["predictors"]) == 1
        self.chart.setVisible(one)
        self.chart.show_fit(fit if one else None, self.titles)

        self.calc_title.setVisible(bool(fit["predictors"]))
        for i, k in enumerate(fit["predictors"]):
            field = QLineEdit(f"{float(np.mean(fit['inputs'][k])):.6g}")
            field.setMaximumWidth(160)
            field.textChanged.connect(lambda _t: self._calculate())
            pu = units_text(fit["predictor_units"].get(k))
            self.calc_inputs.addWidget(QLabel(name(k) + (f" ({pu})" if pu else ""), objectName="FieldName"), i, 0)
            self.calc_inputs.addWidget(field, i, 1)
            self._calc_fields[k] = field
        self._calculate()

    def _draw_formula(self) -> None:
        """Names: left-aligned, one term per line when long. Symbols: one line, centred, shrunk to
        fit the page, with the legend under it."""
        if self.fit is None:
            return
        symbolic = self.symbolic.isChecked()
        room = self.body.width() - 8 if symbolic and self.body.width() > 100 else None
        # symbols: the label takes the page's width so the image can sit centred in it
        policy = QSizePolicy.Policy.Ignored if symbolic else QSizePolicy.Policy.Preferred
        self.formula.setSizePolicy(policy, QSizePolicy.Policy.Preferred)
        self.formula.setPixmap(latex.render(self.fit, self.devicePixelRatioF(), symbolic, room))
        self.formula.setAlignment(Qt.AlignmentFlag.AlignCenter if symbolic else Qt.AlignmentFlag.AlignLeft)
        self._fill_legend()

    def _fill_legend(self) -> None:
        while self.legend.count():
            widget = self.legend.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        if self.fit is None or not self.symbolic.isChecked():
            return
        ratio = self.devicePixelRatioF()
        for row, (symbol, label, units) in enumerate(latex.legend(self.fit)):
            mark = QLabel()
            mark.setPixmap(latex.math_image(f"${symbol}$", theme.SIZES["latex_legend"], ratio))
            mark.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            mark.setFixedHeight(theme.SIZES["legend_row"])
            self.legend.addWidget(mark, row, 0)
            self.legend.addWidget(QLabel(label), row, 1, Qt.AlignmentFlag.AlignVCenter)
            self.legend.addWidget(QLabel(units_text(units), objectName="FieldName"), row, 2, Qt.AlignmentFlag.AlignVCenter)

    def _toggle_symbols(self, on: bool) -> None:
        self.store.setValue("formula_symbols", on)
        self._draw_formula()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        if self.symbolic.isChecked():
            QTimer.singleShot(0, self._draw_formula)  # refit the one-line formula to the new width

    def _copy_latex(self) -> None:
        if self.fit is not None:
            QApplication.clipboard().setText(latex.source(self.fit, self.symbolic.isChecked()))
            self.copy.setText("Copied")
            QTimer.singleShot(1200, lambda: self.copy.setText("Copy LaTeX"))

    def _calculate(self) -> None:
        fit = self.fit
        if fit is None or not self._calc_fields:
            self.calc_result.setText("")
            return
        try:
            values = {k: float(f.text().replace(",", "").strip()) for k, f in self._calc_fields.items()}
        except ValueError:
            self.calc_result.setText("Type a number in every box.")
            return
        g = predict.guess(fit, values)
        units = f" {units_text(fit['units'])}" if fit.get("units") else ""
        outside = [name(k) for k, v in values.items()
                   if not min(fit["inputs"][k]) <= v <= max(fit["inputs"][k])]
        self.calc_result.setText(
            f"{name(fit['target'])} ≈ {number(g['guess'], 5)}{units}\n"
            f"90 % range {number(g['low'], 5)} – {number(g['high'], 5)}{units} · usually off by ± {number(g['typical_error'], 3)}{units}\n"
            f"{g['confidence']:.0%} chance it lands within 5 % of the guess"
            + (f"\nOutside the runs so far for {', '.join(outside)}: a straight line carried past the data." if outside else ""))

    def _relationship_text(self, a: str, b: str) -> str:
        for rel in self.relationships:
            a_keys, b_keys = rel.get("a_keys") or [rel.get("a")], rel.get("b_keys") or [rel.get("b")]
            if (a in a_keys and b in b_keys) or (a in b_keys and b in a_keys):
                sure = rel.get("confidence")
                if not isinstance(sure, (int, float)):
                    return "not scored"
                return (f"{sure:.0%} sure it is real (Efron's local false discovery rate, over {rel.get('n')} "
                        f"runs{self._scope_words()})")
        return "not scored yet"

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
