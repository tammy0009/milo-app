"""The side panel that slides in over the right edge of the graph when a node is tapped.

It floats over the canvas instead of taking a column of its own, so the graph never jumps when
it opens. A simulation shows its full record (the Detail view); any other node shows what it is
and the simulations it is linked to (NodeInfo).
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from milo_app.ui import theme
from milo_app.ui.format import pretty_name, prior_text, sig, units_text

KIND_NAMES = {"sim": "Simulation", "descriptor": "Descriptor", "relationship": "Relationship",
              "prediction": "Prediction"}


class NodeInfo(QWidget):
    """What a descriptor, relationship, or prediction node is, and the simulations it links to."""

    closed = Signal()
    sim_clicked = Signal(str)  # bundle_id

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

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self.links_title = QLabel(objectName="FieldName")
        self.links = QVBoxLayout()
        self.links.setSpacing(2)

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(8)
        box.addWidget(self.kind)
        box.addLayout(head)
        box.addSpacing(4)
        box.addLayout(self.grid)
        box.addSpacing(10)
        box.addWidget(self.links_title)
        box.addLayout(self.links)
        box.addStretch()

    def show_node(self, kind: str, title: str, rows: list[tuple[str, str]], sims: list[tuple[str, str]]) -> None:
        """rows: (label, value) pairs; sims: (bundle_id, title) of the linked simulations."""
        self.kind.setText(KIND_NAMES.get(kind, kind).upper())
        self.title.setText(title)
        for layout in (self.grid, self.links):
            while layout.count():
                widget = layout.takeAt(0).widget()
                if widget:
                    widget.deleteLater()
        for i, (label, value) in enumerate(rows):
            name = QLabel(label, objectName="FieldName")
            text = QLabel(value, wordWrap=True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(name, i, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(text, i, 1)
        self.links_title.setText(f"LINKED SIMULATIONS  {len(sims)}" if sims else "")
        for bundle_id, sim_title in sims:
            link = QPushButton(objectName="LinkButton")
            room = theme.SIZES["drawer"] - 64  # panel width less margins and button padding
            link.setText(link.fontMetrics().elidedText(sim_title, Qt.TextElideMode.ElideRight, room))
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setToolTip(f"{sim_title}\n{bundle_id}")
            link.clicked.connect(lambda _=False, b=bundle_id: self.sim_clicked.emit(b))
            self.links.addWidget(link)


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
