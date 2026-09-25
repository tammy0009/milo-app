"""The side panel that slides in over the right edge of the graph when a node is tapped.

It floats over the canvas instead of taking a column of its own, so the graph never jumps when
it opens. A simulation shows its full record (the Detail view); any other node shows what it is
and the simulations it is linked to (NodeInfo).

Ghosts and relationships lead with what matters: how sure (a big percentage and a bar, red to
green), then what changes. For a ghost, the knob it moves and what it expects to move with it, next
to the run it came from; for a relationship, one sentence. All the math is one click away, under
"Show the math".
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from milo_app.ui import theme
from milo_app.ui.format import pretty_name, prior_text, short_title, sig, units_text

KIND_NAMES = {"sim": "Simulation", "descriptor": "Descriptor", "relationship": "Relationship",
              "prediction": "Prediction"}


class ConfidenceBar(QWidget):
    """A thin bar filled to the confidence, in its color (red unsure, green sure)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(theme.SIZES["confidence_bar"])
        self.value = 0.0

    def set_value(self, value: float) -> None:
        self.value = min(max(value, 0.0), 1.0)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self.height() / 2
        painter.setBrush(QColor(theme.COLORS["line"]))
        painter.drawRoundedRect(QRectF(self.rect()), r, r)
        if self.value > 0:
            painter.setBrush(QColor(theme.confidence_color(self.value)))
            painter.drawRoundedRect(QRectF(0, 0, max(self.width() * self.value, 2 * r), self.height()), r, r)


class NodeInfo(QWidget):
    """What a descriptor, relationship, or prediction node is, and the simulations it links to."""

    closed = Signal()
    sim_clicked = Signal(str)  # bundle_id
    isolate_toggled = Signal(str, bool)  # campaign, on: work its ghosts out from its own runs only

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Detail")
        self.kind = QLabel(objectName="KindLabel")
        self.title = QLabel(objectName="SimTitle", wordWrap=True)
        close = QPushButton("✕", objectName="IconButton")
        close.clicked.connect(self.closed)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

        # the summary: confidence first, then what changes (ghosts and relationships)
        self.summary = QWidget()
        self.sure = QLabel(objectName="SureBig")
        self.sure_note = QLabel(objectName="FieldName", wordWrap=True)
        self.bar = ConfidenceBar()
        self.headline_label = QLabel(objectName="KindLabel")
        self.headline = QLabel(objectName="Headline", wordWrap=True)
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.isolate = QPushButton("Isolate", objectName="Chip", checkable=True)
        self.isolate.toggled.connect(lambda on: self._campaign and self.isolate_toggled.emit(self._campaign, on))
        self._campaign: str | None = None
        self.changes_label = QLabel(objectName="KindLabel")
        self.changes = QGridLayout()
        self.changes.setHorizontalSpacing(10)
        self.changes.setVerticalSpacing(7)
        self.changes.setColumnStretch(0, 1)
        self.more = QLabel(objectName="FieldName", wordWrap=True)
        sure_row = QHBoxLayout()
        sure_row.setSpacing(10)
        sure_row.addWidget(self.sure, 0, Qt.AlignmentFlag.AlignBottom)
        sure_row.addWidget(self.sure_note, 1, Qt.AlignmentFlag.AlignBottom)
        s = QVBoxLayout(self.summary)
        s.setContentsMargins(0, 0, 0, 0)
        s.setSpacing(8)
        s.addLayout(sure_row)
        s.addWidget(self.bar)
        s.addWidget(self.isolate, 0, Qt.AlignmentFlag.AlignLeft)
        s.addSpacing(10)
        s.addWidget(self.headline_label)
        s.addWidget(self.headline)
        s.addSpacing(8)
        s.addWidget(self.changes_label)
        s.addLayout(self.changes)
        s.addWidget(self.more)
        self.math = QPushButton("Show the math", objectName="Chip", checkable=True)
        self.math.toggled.connect(self._toggle_math)

        self.details = QWidget()
        self.sections = QVBoxLayout(self.details)
        self.sections.setContentsMargins(0, 4, 0, 0)
        self.sections.setSpacing(6)
        self.links_title = QLabel(objectName="FieldName")
        self.links = QVBoxLayout()
        self.links.setSpacing(2)

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(8)
        box.addWidget(self.kind)
        box.addLayout(head)
        box.addSpacing(4)
        box.addWidget(self.summary)
        box.addWidget(self.math, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.details)
        box.addSpacing(10)
        box.addWidget(self.links_title)
        box.addLayout(self.links)
        box.addStretch()

    def show_node(self, kind: str, title: str, rows: list[tuple[str, str]] | list[dict[str, Any]],
                  sims: list[tuple[str, str]], summary: dict[str, Any] | None = None) -> None:
        """rows: (label, value) pairs, or sections (see _add_section); sims: (bundle_id, title) of the linked
        simulations. With a summary (ghosts, relationships), it leads and the rows wait under "Show the
        math"."""
        self.kind.setText(KIND_NAMES.get(kind, kind).upper())
        self.title.setText(title)
        self._fill_summary(summary)
        self.summary.setVisible(summary is not None)
        self.math.setVisible(summary is not None and bool(rows))
        self.math.blockSignals(True)
        self.math.setChecked(False)
        self.math.blockSignals(False)
        self.details.setVisible(summary is None)
        _clear(self.sections)
        _clear(self.links)
        sections = rows if rows and isinstance(rows[0], dict) else [{"rows": rows}] if rows else []
        for section in sections:
            self._add_section(section)
        self.links_title.setText(f"LINKED SIMULATIONS  {len(sims)}" if sims else "")
        for bundle_id, sim_title in sims:
            link = QPushButton(objectName="LinkButton")
            room = theme.SIZES["drawer"] - 64  # panel width less margins and button padding
            link.setText(link.fontMetrics().elidedText(sim_title, Qt.TextElideMode.ElideRight, room))
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setToolTip(f"{sim_title}\n{bundle_id}")
            link.clicked.connect(lambda _=False, b=bundle_id: self.sim_clicked.emit(b))
            self.links.addWidget(link)

    def minimumSizeHint(self):  # noqa: N802 - Qt's name
        """As narrow as the panel: its text wraps to fit, so nothing is cut off at the right edge."""
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def _add_section(self, section: dict[str, Any]) -> None:
        """One block of the math: {"title"} over any of {"rows": [(label, value)]}, {"table": {"columns",
        "rows", "sure": index of a confidence column, "align_left": first columns left-aligned}} (a row
        that is a plain string is a group heading), and {"note"}."""
        if section.get("title"):
            head = QLabel(section["title"], objectName="KindLabel")
            self.sections.addSpacing(8)
            self.sections.addWidget(head)
        if section.get("rows"):
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(5)
            grid.setColumnStretch(1, 1)
            for i, (label, value) in enumerate(section["rows"]):
                name = QLabel(label, objectName="FieldName")
                text = QLabel(value, wordWrap=True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(name, i, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(text, i, 1)
            self.sections.addLayout(grid)
        table = section.get("table")
        if table:
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(table.get("stretch", 0), 1)  # the column that takes the spare width
            columns, sure, left = table["columns"], table.get("sure"), table.get("align_left", 1)
            for j, name in enumerate(columns):
                label = QLabel(name, objectName="FieldName")
                label.setAlignment(Qt.AlignmentFlag.AlignLeft if j < left else Qt.AlignmentFlag.AlignRight)
                grid.addWidget(label, 0, j)
            for i, row in enumerate(table["rows"], start=1):
                if isinstance(row, str):  # a group heading
                    heading = QLabel(row, objectName="TableGroup")
                    grid.addWidget(heading, i, 0, 1, len(columns))
                    continue
                for j, cell in enumerate(row):
                    if j == sure and isinstance(cell, (int, float)):
                        label = QLabel(f"{cell:.0%}", objectName="ChangeSure")
                        label.setStyleSheet(f"color: {theme.confidence_color(cell)};")
                    else:
                        label = QLabel(str(cell), objectName="TableCell", wordWrap=True)  # never wider than the panel
                    label.setAlignment((Qt.AlignmentFlag.AlignLeft if j < left else Qt.AlignmentFlag.AlignRight)
                                       | Qt.AlignmentFlag.AlignTop)
                    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    grid.addWidget(label, i, j)
            self.sections.addLayout(grid)
        if section.get("note"):
            note = QLabel(section["note"], objectName="FieldName", wordWrap=True)
            note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.sections.addWidget(note)

    def _toggle_math(self, on: bool) -> None:
        self.details.setVisible(on)
        self.math.setText("Hide the math" if on else "Show the math")

    def _fill_summary(self, summary: dict[str, Any] | None) -> None:
        while self.changes.count():
            widget = self.changes.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        if summary is None:
            return
        sure = summary.get("confidence")
        if isinstance(sure, (int, float)):
            self.sure.setText(f"{sure:.0%}" if sure >= 0.01 or sure < 0.0005 else f"{sure:.1%}")
            self.sure.setStyleSheet(f"color: {theme.confidence_color(sure)};")
            self.bar.set_value(sure)
        else:
            self.sure.setText("–")
            self.sure.setStyleSheet(f"color: {theme.COLORS['muted']};")
            self.bar.set_value(0.0)
        self.sure_note.setText(summary.get("confidence_note", ""))
        self.headline_label.setText(summary.get("headline_label", ""))
        self.headline.setText(summary.get("headline", ""))
        self.headline_label.setVisible(bool(summary.get("headline_label")))
        rows = summary.get("changes") or []
        self.changes_label.setText(summary.get("changes_label", "") if rows else "")
        self.changes_label.setVisible(bool(rows))
        for i, row in enumerate(rows):
            name = QLabel(row["name"], wordWrap=True)
            name.setToolTip(row.get("tip", row["name"]))
            move = QLabel(row["move"], objectName="ChangeValue", wordWrap=True)
            move.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            move.setToolTip(row.get("tip", ""))
            self.changes.addWidget(name, i, 0, Qt.AlignmentFlag.AlignTop)
            self.changes.addWidget(move, i, 1, Qt.AlignmentFlag.AlignTop)
            if isinstance(row.get("confidence"), (int, float)):
                c = QLabel(f"{row['confidence']:.0%}", objectName="ChangeSure")
                c.setStyleSheet(f"color: {theme.confidence_color(row['confidence'])};")
                c.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
                c.setToolTip("how sure it is of this value (within 5 %)")
                self.changes.addWidget(c, i, 2, Qt.AlignmentFlag.AlignTop)
        self.more.setText(summary.get("more", ""))
        self.more.setVisible(bool(summary.get("more")))
        isolate = summary.get("isolate")  # {"campaign", "on"} for a ghost of a campaign's run
        self._campaign = isolate["campaign"] if isolate else None
        self.isolate.setVisible(isolate is not None)
        self.isolate.blockSignals(True)
        self.isolate.setChecked(bool(isolate and isolate["on"]))
        self.isolate.blockSignals(False)
        if isolate:
            self.isolate.setText(f"Isolated to {isolate['campaign']}" if isolate["on"] else f"Isolate {isolate['campaign']}")
            self.isolate.setToolTip("Work this campaign's ghosts out from its own runs only (its ring turns while on)"
                                    if not isolate["on"] else "Click to use every run again")


def _clear(layout) -> None:
    """Empty a layout, nested layouts included."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


class Drawer(QFrame):
    """Slides in from the right edge of `host` and back out again."""

    def __init__(self, host: QWidget, detail: QWidget, info: QWidget) -> None:
        super().__init__(host)
        self.host = host
        self.setObjectName("Drawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(theme.SIZES["drawer"])
        self.is_open = False

        self.pages = QStackedWidget()
        for page in (detail, info):
            scroll = QScrollArea(widgetResizable=True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        box = QVBoxLayout(self)
        box.setContentsMargins(1, 0, 0, 0)  # room for the left border line
        box.addWidget(self.pages)

        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(theme.SIZES["drawer_ms"])
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._settled)
        host.installEventFilter(self)
        self.hide()

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.pages.currentWidget().verticalScrollBar().setValue(0)
        if not self.is_open:
            self.is_open = True
            self.setFixedHeight(self.host.height())
            self.move(self._x(False), 0)
            self.show()
            self.raise_()
            self._slide(self._x(True))

    def close_drawer(self) -> None:
        if self.is_open:
            self.is_open = False
            self._slide(self._x(False))

    def _x(self, shown: bool) -> int:
        return self.host.width() - self.width() if shown else self.host.width()

    def _slide(self, x: int) -> None:
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(x, 0))
        self.anim.start()

    def _settled(self) -> None:
        if not self.is_open:
            self.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if watched is self.host and event.type() == QEvent.Type.Resize:
            self.setFixedHeight(self.host.height())
            if self.anim.state() != QPropertyAnimation.State.Running:
                self.move(self._x(self.is_open), 0)
        return False


def loads(value: Any, default: Any) -> Any:
    """A stored field that may be JSON text (lists of lists, tables) or already a value."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default if value is None else value


def _num(value: Any, digits: int = 4) -> str:
    return sig(value, digits) if isinstance(value, (int, float)) else str(value)


def _field(key: str) -> str:
    """"output:AmorphousCell.CellVolume" -> "Amorphous Cell Cell Volume"."""
    return pretty_name(key.partition(":")[2] if ":" in key else key)


def _units(units: Any) -> str:
    return f" {units_text(units)}" if units else ""


def node_rows(data: dict[str, Any], models: list[dict[str, Any]] | None = None,
              titles: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """The facts, and the math, for a non-simulation node, as (label, value) rows."""
    titles = titles or {}
    kind = data["kind"]
    if kind == "descriptor":
        rows = [("Field", _field(data["group"])),
                ("From", {"bundle": "bundle.json", "input": "Inputs", "output": "Outputs"}.get(
                    data["group"].partition(":")[0], "")),
                ("Exact value" if len(data["exact"]) == 1 else "Exact values", "\n".join(data["exact"]))]
        for model in sorted(models or [], key=lambda m: -(m.get("n") or 0)):
            rows += model_rows(model, titles, several=len(models) > 1)
        return rows
    if kind == "relationship":
        return relationship_rows(data["rel"], titles)
    if kind == "prediction":
        return prediction_rows(data["pred"])
    return []


def model_rows(m: dict[str, Any], titles: dict[str, str], several: bool = False) -> list[tuple[str, str]]:
    """How this descriptor is guessed (ghost.md 4.1 and 4.2), with the fitted numbers. With several
    families of runs, each has its own model, headed by the knobs its runs vary."""
    rows = [("MODEL", f"for the runs varying {m['family']}" if several and m.get("family") else "")]
    aliases = [a for a in (m.get("aliases") or []) if a != m.get("descriptor")]
    if aliases:
        rows.append(("Same as", "\n".join(_field(a) for a in aliases)))
    if m.get("method") == "rule of succession":
        counts = loads(m.get("counts"), {})
        n, k = m.get("n"), m.get("categories")
        seen = counts.get(str(m.get("guess")), 0)
        rows += [
            ("Method", "Laplace's rule of succession (ghost.md 4.2)"),
            ("Seen", "\n".join(f"{v}: {c} of {n} runs" for v, c in counts.items())),
            ("Guess", str(m.get("guess"))),
            ("Confidence", f"(times seen + 1) / (runs + K)\n= ({seen} + 1) / ({n} + {k}) = {m.get('confidence', 0):.1%}\n"
                           f"K = {k - 1} value{'s' if k != 2 else ''} seen + 1 for \"something new\""),
        ]
    else:
        units = _units(m.get("units"))
        knobs = m.get("predictors") or m.get("knobs") or []
        terms = [f"{s:+.4g} × {_knob(k)}" for k, s in zip(knobs, m.get("slopes") or [])]
        rows += [
            ("Method", f"Bayesian linear regression on the knobs, fitted to {m.get('n')} runs (ghost.md 4.1)"
                       + (f"\nleft out, the same in all these runs: {', '.join(_knob(k) for k in m['left_out'])}"
                          if m.get("left_out") else "")),
            ("Equation", f"{m.get('name')} = {_num(m.get('intercept'), 6)} " + " ".join(terms) + units),
        ]
        for k, s, e in zip(knobs, m.get("slopes") or [], m.get("slope_sd") or []):
            rows.append((f"Slope, {_knob(k)}", f"{s:+.4g} ± {e:.3g}{units} per unit\n(± is the slope's own uncertainty)"))
        if m.get("noise_sd") is not None:
            rows.append(("Run-to-run scatter", f"± {_num(m['noise_sd'], 3)}{units} (estimated)"))
        runs = [f"{titles.get(s, s)}: {_num(o, 6)} → fitted {_num(f, 6)}"
                for s, o, f in zip(m.get("sims") or [], m.get("observed") or [], m.get("fitted") or [])]
        rows += [
            ("Runs", "\n".join(runs)),
            ("Prior", prior_text(m.get("prior"))),
            ("A guess", "Student-t with mean = the equation at the chosen knobs,\n"
                        f"{_num(m.get('df'), 3)} degrees of freedom; 90 % range and confidence from it"),
        ]
    rows.append(("Calculation", str(m.get("calc_id", ""))))
    return rows


def relationship_rows(r: dict[str, Any], titles: dict[str, str]) -> list[tuple[str, str]]:
    """The relationship's confidence worked out step by step (ghost.md 5.3)."""
    a, b = _field(r.get("a", "")), _field(r.get("b", ""))
    sure = r.get("confidence")
    rows = [("Confidence", f"{sure:.1%} that this relationship is real" if isinstance(sure, (int, float)) else "not given"),
            ("Kind", "cause: a knob drives the other" if r.get("kind") == "cause"
             else "association: they move together (usually through a shared knob)")]
    if r.get("source") != "calculation":
        return rows + [(pretty_name(k), str(v)) for k, v in r.items()
                       if k not in ("id", "name", "a", "b", "confidence", "kind", "source")]
    points = loads(r.get("points"), [])
    rows.append((f"Runs used ({r.get('n')})", "\n".join(
        f"{titles.get(s, s)}: {_num(x, 6)}{_units(r.get('a_units'))}, {_num(y, 6)}{_units(r.get('b_units'))}"
        for s, x, y in points)))
    if "r" not in r:
        return rows + [("Why no score", r.get("note", "not enough runs where both change"))]
    rows.append(("1  Correlation", f"r = {r['r']:+.4f}  (Pearson, {a} vs {b})"))
    if "t" not in r:
        return rows + [("Why no score", r.get("note", "needs at least 3 runs"))]
    null = (f"estimated from all {r.get('pairs_tested')} pairs (Efron's empirical null):\n"
            f"unrelated pairs' z ~ Normal({r['null_mean']:.3f}, {r['null_sd']:.3f}²)"
            if r.get("null") == "empirical" else
            f"textbook: unrelated pairs' z ~ Normal(0, 1)\n(too few pairs, {r.get('pairs_tested')}, to estimate it)")
    rows += [
        ("2  Test statistic", f"t = r·√(n − 2) / √(1 − r²) = {r['t']:.4g}\nwith {r['df']} degree{'s' if r['df'] != 1 else ''} of freedom"),
        ("3  p-value", f"p = {r['p']:.4g}  (two-sided t-test)"),
        ("4  z-score", f"z = {r['z']:+.4f}  (the p-value on the normal scale)"),
        ("5  Null", null),
        ("6  Share unrelated", f"π0 = {r['pi0']:.4f}  (estimated)" if r.get("null") == "empirical" else "π0 = 1 (assumed)"),
        ("7  Local FDR", f"lfdr = π0 · f0(z) / f(z) = {r['lfdr']:.4f}\nthe probability this relationship is NOT real"),
        ("8  Confidence", f"1 − lfdr = {sure:.4f}"),
        ("Method", "Efron's local false discovery rate\n(statsmodels local_fdr, NullDistribution)"),
        ("Calculation", str(r.get("calc_id", ""))),
    ]
    return rows


# ---------------------------------------------------------------------------- summaries (what leads the panel)

CHANGES_SHOWN = 10  # the biggest expected changes listed; the rest are counted
SAME_BELOW = 0.01  # a guess within 1 % of the run it came from counts as "about the same"


def _move(before: Any, after: Any, units: Any) -> str:
    return f"{_num(before, 4)} → {_num(after, 4)}{_units(units)}"


def prediction_summary(pred: dict[str, Any], base: dict[str, tuple[Any, Any]] | None,
                       base_title: str = "") -> dict[str, Any]:
    """A ghost: how sure, the knob it moves, and what it expects to move with it (vs the run it came from)."""
    from milo_app.ui.format import ghost_change

    sure = pred.get("confidence")
    _short, change = ghost_change(pred)
    summary: dict[str, Any] = {"confidence": sure}
    if pred.get("source") == "verified" and pred.get("values"):
        summary |= {"confidence": pred["right"] / pred["values"],
                    "confidence_note": f"came out right: {pred['right']} of {pred['values']} values within 5 % "
                                       f"(it expected {pred['stated']:.0%})" if isinstance(pred.get("stated"), (int, float))
                                       else f"came out right: {pred['right']} of {pred['values']} values within 5 %"}
    else:
        summary["confidence_note"] = ("sure, on average, that its guesses land within 5 %"
                                      if pred.get("source") != "mcp" else "the MCP's own confidence")
        if pred.get("isolated"):
            summary["confidence_note"] += f", from only the {pred.get('sims_used')} runs in {pred['isolated']}"
    summary |= {"headline_label": "WHAT IT CHANGES" + (f" FROM {base_title.upper()}" if base_title else ""),
                "headline": change or "—"}
    guesses = loads(pred.get("predicted_json"), {})
    moves, same = [], 0
    for key, g in guesses.items():
        g = g if isinstance(g, dict) else {"guess": g}
        full = key if ":" in key else f"output:{key}"
        after = g.get("guess", g.get("value"))
        before = (base or {}).get(full, (None, None))[0]
        units = g.get("units")
        if isinstance(after, (int, float)) and isinstance(before, (int, float)):
            shift = abs(after - before) / max(abs(before), 1e-12)
            if shift < SAME_BELOW:
                same += 1
                continue
            moves.append((shift, {"name": _field(full), "move": _move(before, after, units), "confidence": g.get("confidence"),
                                  "tip": f"{_field(full)}\nthe run: {_num(before, 6)}{_units(units)}\n"
                                         f"this ghost: {_num(after, 6)}{_units(units)} ({(after - before) / max(abs(before), 1e-12):+.1%})"}))
        elif base is None and isinstance(after, (int, float)):  # a ghost with no run to compare with
            moves.append((0.0, {"name": _field(full), "move": f"{_num(after, 4)}{_units(units)}", "confidence": g.get("confidence")}))
        # text (dates, titles, names) is bookkeeping, not a prediction: left to Show the math
    # the changes it is sure of first (at least even odds), each group biggest first
    moves.sort(key=lambda m: (-(isinstance(m[1].get("confidence"), (int, float)) and m[1]["confidence"] >= 0.5), -m[0]))
    summary["changes_label"] = "WHAT IT EXPECTS TO CHANGE" if base else "WHAT IT EXPECTS"
    summary["changes"] = [m for _s, m in moves[:CHANGES_SHOWN]]
    extra = len(moves) - CHANGES_SHOWN
    notes = []
    if extra > 0:
        notes.append(f"{extra} more change{'s' if extra != 1 else ''} (Show the math lists every guess)")
    if same:
        notes.append(f"{same} other value{'s' if same != 1 else ''} stay about the same (within 1 %)")
    summary["more"] = "\n".join(notes)
    return summary


def _stage(key: str) -> tuple[str, str]:
    """(group, name) for a descriptor: "output:NPT.final_frame.Density" -> ("NPT", "Final Frame Density")."""
    source, _, name = key.partition(":") if ":" in key else ("output", "", key)
    if source == "input":
        return "Settings", pretty_name(name.removeprefix("requested."))
    if source == "bundle":
        return "Run", pretty_name(name)
    stage, _, rest = name.partition(".")
    return (pretty_name(stage), pretty_name(rest)) if rest else ("Results", pretty_name(stage))


def prediction_math(pred: dict[str, Any], titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Under a ghost's "Show the math": how its confidence is built, the run it proposes, every guess,
    and where it comes from."""
    sections: list[dict[str, Any]] = []
    made_by = pred.get("made_by") if pred.get("source") == "verified" else pred.get("source")
    guesses = loads(pred.get("predicted_json"), {})
    if made_by == "mcp":
        sections.append({"title": "HOW SURE", "rows": [("Confidence", f"{pred.get('confidence', 0):.1%}, the MCP's own")],
                         "note": str(pred.get("reasoning") or "")})
    elif pred.get("values_guessed"):
        weakest = pred.get("weakest")
        sections.append({"title": "HOW SURE", "rows": [
            ("Overall", f"{pred.get('confidence', 0):.1%}: the average over all {pred['values_guessed']} values it guesses"),
            ("Each value", "the chance the real value lands within 5 % of the guess,\n"
                           "from its Student-t range: 2·T(tolerance / spread) − 1"),
            ("Weakest", f"{_field(weakest)}, {pred.get('weakest_confidence', 0):.0%}" if weakest else "none"),
        ]})
    if pred.get("source") == "verified":
        sections.append({"title": "HOW IT CAME OUT", "rows": [
            ("Run", (titles or {}).get(pred.get("verified_by", ""), str(pred.get("verified_by", "")))),
            ("Right", f"{pred.get('right')} of {pred.get('values')} values within 5 %"),
            ("In range", f"{pred.get('inside')} of {pred.get('ranged')} numbers inside their 90 % ranges")]})
    inputs = loads(pred.get("inputs_json"), {})
    setting_rows = []
    for key, v in inputs.items():
        v = v if isinstance(v, dict) else {"value": v}
        value = f"{_num(v['was'], 6)} → {_num(v.get('value'), 6)}" if "was" in v else _num(v.get("value"), 6)
        setting_rows.append([_knob(key), value + _units(v.get("units"))])
    if setting_rows:
        sections.append({"title": "THE RUN IT PROPOSES", "table": {"columns": ["Setting", "Value"], "rows": setting_rows}})
    rows: list[Any] = []
    group = None
    for key in sorted(guesses, key=lambda k: _stage(k if ":" in k else f"output:{k}")):
        g = guesses[key] if isinstance(guesses[key], dict) else {"guess": guesses[key]}
        stage, name = _stage(key if ":" in key else f"output:{key}")
        if stage != group:
            rows.append(stage)
            group = stage
        value = g.get("guess", g.get("value"))
        if isinstance(g.get("low"), (int, float)):
            spread = f"{_num(g['low'], 4)} – {_num(g['high'], 4)}"
        elif isinstance(g.get("spread"), (int, float)):
            spread = f"± {_num(g['spread'], 3)}"
        else:
            spread = ""
        rows.append([name, f"{_num(value, 5)}{_units(g.get('units'))}", spread, g.get("confidence", "")])
    if rows:
        sections.append({"title": f"EVERY GUESS  {len(guesses)}",
                         "table": {"columns": ["Value", "Guess", "90 % range", "Sure"], "rows": rows, "sure": 3}})
    source = [("Made by", "the MCP, by reasoning" if made_by == "mcp" else "MILO, by calculation")]
    if pred.get("isolated"):
        source.append(("Runs", f"only the {pred.get('sims_used')} runs in {pred['isolated']} (isolated)"))
    elif pred.get("sims_used"):
        source.append(("Runs", f"every run with the same knobs ({pred.get('sims_used')} in the graph)"))
    if pred.get("model"):
        source.append(("Model", str(pred["model"])))
    if pred.get("calc_id"):
        source.append(("Calculation", str(pred["calc_id"])))
    sections.append({"title": "WHERE IT COMES FROM", "rows": source, "note": "The rules: ghost.md, sections 4 and 5."})
    return sections


def relationship_math(r: dict[str, Any], titles: dict[str, str]) -> list[dict[str, Any]]:
    """Under a relationship's "Show the math": the runs it rests on, and its confidence step by step."""
    a, b = _field(r.get("a", "")), _field(r.get("b", ""))
    points = loads(r.get("points"), [])
    sections: list[dict[str, Any]] = []
    if points:
        sections.append({"title": f"THE RUNS  {len(points)}", "table": {
            "columns": ["Run", a + _units(r.get("a_units")), b + _units(r.get("b_units"))],
            "rows": [[short_title(titles.get(s, s), 26), _num(x, 5), _num(y, 5)] for s, x, y in points]}})
    if "t" not in r:
        return sections + [{"title": "WHY NO SCORE", "note": r.get("note", "needs at least 3 runs where both change")}]
    null = (f"from all {r.get('pairs_tested')} pairs: z of unrelated pairs ~ Normal({r['null_mean']:.2f}, {r['null_sd']:.2f}²)"
            if r.get("null") == "empirical" else f"the textbook one, Normal(0, 1) (only {r.get('pairs_tested')} pairs)")
    steps = [
        ["1", "Correlation", f"r = {r['r']:+.4f}"],
        ["2", "Test statistic", f"t = {r['t']:.4g}  ({r['df']} df)"],
        ["3", "p-value", f"p = {r['p']:.3g}"],
        ["4", "On the normal scale", f"z = {r['z']:+.3f}"],
        ["5", "Unrelated pairs look like", null],
        ["6", "Share unrelated", f"π0 = {r['pi0']:.3f}" + ("" if r.get("null") == "empirical" else " (assumed)")],
        ["7", "Chance it is NOT real", f"lfdr = {r['lfdr']:.3f}"],
        ["8", "Confidence", f"1 − lfdr = {r.get('confidence', 0):.1%}"],
    ]
    sections.append({"title": "HOW SURE, STEP BY STEP",
                     "table": {"columns": ["", "Step", "Result"], "rows": steps, "align_left": 2, "stretch": 1}})
    sections.append({"title": "WHERE IT COMES FROM", "rows": [
        ("Method", "Efron's local false discovery rate (statsmodels)"),
        ("Kind", "a knob drives it" if r.get("kind") == "cause" else "they move together (often through a shared knob)"),
        ("Calculation", str(r.get("calc_id", "")))], "note": "The rules: ghost.md, section 5.3."})
    return sections


STRENGTH = ((0.9, "closely"), (0.6, "clearly"), (0.3, "loosely"), (0.0, "barely"))


def relationship_summary(r: dict[str, Any]) -> dict[str, Any]:
    """A relationship: how sure it is real, and in one sentence what it says."""
    a, b = _field(r.get("a", "")), _field(r.get("b", ""))
    sure = r.get("confidence")
    n = r.get("n")
    if "r" not in r:
        headline = f"{a} and {b}: not enough runs where both change to tell"
    else:
        rho = r["r"]
        word = next(w for limit, w in STRENGTH if abs(rho) >= limit)
        way = "goes up" if rho > 0 else "goes down"
        headline = f"When {a} goes up, {b} {way}, {word}."
    note = ("sure the link is real, not chance" if "z" in r else "needs at least 3 runs where both change")
    return {"confidence": sure if "z" in r else None, "confidence_note": note,
            "headline_label": "WHAT IT SAYS", "headline": headline,
            "changes": [], "more": (f"From {n} run{'s' if n != 1 else ''}"
                                    + (f" · correlation r = {r['r']:+.2f}" if "r" in r else "")
                                    + (" · a knob drives it" if r.get("kind") == "cause" else ""))}


def _knob(key: str) -> str:
    name = _field(key)
    return name[len("Requested "):] if name.startswith("Requested ") else name


def prediction_rows(pred: dict[str, Any]) -> list[tuple[str, str]]:
    """A ghost's panel: who made it, how sure and why, the run it proposes, and every guess."""
    sure = pred.get("confidence")
    made_by = pred.get("made_by") if pred.get("source") == "verified" else pred.get("source")
    rows = [("Made by", "the MCP, by reasoning" if made_by == "mcp" else "MILO, by calculation"),
            ("Confidence", f"{sure:.1%}" if isinstance(sure, (int, float)) else "not given")]
    if pred.get("source") == "verified":
        stated = pred.get("stated")
        rows.insert(0, ("VERIFIED", "this ghost was really run, then checked"))
        rows += [("Run", str(pred.get("verified_by", ""))),
                 ("Came out", f"{pred.get('right')} of {pred.get('values')} values right (within 5 %)"
                  + (f"; it expected {stated:.0%}" if isinstance(stated, (int, float)) else "")
                  + (f"\n{pred.get('inside')} of {pred.get('ranged')} numbers inside their 90 % ranges"
                     if pred.get("ranged") else ""))]
    if made_by == "mcp":
        if pred.get("reasoning"):
            rows.append(("Reasoning", str(pred["reasoning"])))
    elif pred.get("values_guessed"):
        weakest = pred.get("weakest")
        rows += [
            ("Why", f"the average of the confidences of all {pred['values_guessed']} values it guesses:\n"
                    "the share of its guesses expected to come out right (ghost.md 5.2)"),
            ("Weakest", f"{_field(weakest)} at {pred.get('weakest_confidence', 0):.0%}" if weakest else "none"),
            ("One value", "P(real within 5 % of the guess) = 2·T_df(tolerance / spread) − 1\n(ghost.md 5.1)"),
            ("Based on", f"{pred.get('sims_used')} simulations"),
        ]
    if pred.get("model"):
        rows.append(("Model", str(pred["model"])))
    inputs = loads(pred.get("inputs_json"), {})
    rows.append(("Proposed run", "\n".join(
        f"{_knob(k)}: {_num(v.get('value') if isinstance(v, dict) else v, 6)}"
        f"{_units(v.get('units') if isinstance(v, dict) else None)}" for k, v in inputs.items())))
    guesses = loads(pred.get("predicted_json"), {})
    lines = []
    for key in sorted(guesses, key=_field):
        g = guesses[key] if isinstance(guesses[key], dict) else {"guess": guesses[key]}
        value = g.get("guess", g.get("value"))
        units = _units(g.get("units"))
        line = f"{_field(key)}: {_num(value, 5)}{units}"
        if isinstance(g.get("low"), (int, float)):
            line += f"  (90 %: {_num(g['low'], 5)} – {_num(g['high'], 5)})"
        elif isinstance(g.get("spread"), (int, float)):
            line += f" ± {_num(g['spread'], 3)}"
        if isinstance(g.get("confidence"), (int, float)):
            line += f" · {g['confidence']:.0%}"
        lines.append(line)
    if lines:
        rows.append((f"Guesses ({len(lines)})", "\n".join(lines)))
    if pred.get("calc_id"):
        rows.append(("Calculation", str(pred["calc_id"])))
    return rows
