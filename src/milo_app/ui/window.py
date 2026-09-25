"""The MILO window: a starting screen while Docker and Neo4j come up, then the simulations.

Layout of the main view:
    TopBar       MILO wordmark · Graph | Ghosts · search · status · Fit · Scan now
    Descriptors  every field the simulations carry; tick one to put it on the graph (left)
    TypeBar      which kinds of node are shown: simulations, descriptors, relationships, predictions
    Graph        the nodes of those kinds (middle)
    Drawer       slides over the graph's right edge when a selected node's little circle is held:
                 a simulation's full record, or what any other node is and the sims it links to

The Ghosts tab (formulas.py) swaps the whole area below the top bar for the formula finder.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from neo4j import Driver
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from milo_app import graph, predict, services
from milo_app.config import ASSETS, get_settings
from milo_app.ui import prefs, theme
from milo_app.ui.campaigns import color_of as campaign_color
from milo_app.ui.descriptors import DescriptorPanel
from milo_app.ui.drawer import (
    Drawer, NodeInfo, descriptor_math, descriptor_summary, loads, node_rows, prediction_math, prediction_summary,
    relationship_math, relationship_summary,
)
from milo_app.ui.format import (
    elapsed, ghost_title, pretty_name, quantity, short_title, shown_value, sig, size_text, test_summary,
)
from milo_app.ui.formulas import FormulaView
from milo_app.ui.graph_view import GraphView
from milo_app.ui.typebar import TypeBar
from milo_app.watcher import Watcher

SUMMARY = (("Product", "product"), ("Module", "module"), ("Task", "task"), ("Status", "status"),
           ("Start", "start"), ("Finish", "finish"), ("Elapsed", None), ("Folder", "source_path"))


# ---------------------------------------------------------------------------- starting screen


class Starter(QObject):
    """Runs services.start_all on a thread and reports back to the window."""

    progress = Signal(str)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, driver: Driver) -> None:
        super().__init__()
        self.driver = driver

    def start(self) -> None:
        threading.Thread(target=self._run, name="milo-services", daemon=True).start()

    def _run(self) -> None:
        try:
            services.start_all(self.progress.emit, lambda: graph.is_ready(self.driver))
            graph.init_schema(self.driver)
        except Exception as exc:  # noqa: BLE001 - shown on the starting screen
            self.failed.emit(str(exc))
            return
        self.ready.emit()


class Splash(QWidget):
    retry = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Splash")
        wordmark = QLabel("MILO", objectName="SplashWordmark", alignment=Qt.AlignmentFlag.AlignCenter)
        wordmark.setFont(theme.wordmark_font("splash"))
        self.detail = QLabel("Starting MILO…", objectName="SplashDetail", alignment=Qt.AlignmentFlag.AlignCenter)
        self.error = QLabel(objectName="SplashError", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.error.setMaximumWidth(560)
        self.error.hide()
        self.retry_button = QPushButton("Try again")
        self.retry_button.clicked.connect(self.retry)
        self.retry_button.hide()

        box = QVBoxLayout(self)
        box.addStretch()
        box.addWidget(wordmark)
        box.addSpacing(16)
        box.addWidget(self.detail)
        box.addWidget(self.error, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addSpacing(12)
        box.addWidget(self.retry_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addStretch()

    def show_progress(self, text: str) -> None:
        self.detail.setText(text)
        self.error.hide()
        self.retry_button.hide()

    def show_failure(self, error: str) -> None:
        self.detail.setText("MILO could not start")
        self.error.setText(error)
        self.error.show()
        self.retry_button.show()


# ---------------------------------------------------------------------------- detail panel


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setShowGrid(False)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)  # names can be long: drag to widen
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # the value / file column takes the room
    header.resizeSection(0, 150)
    return table


class Detail(QWidget):
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Detail")
        self.folder: Path | None = None

        self.empty = QLabel("Select a simulation", objectName="Empty", alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(objectName="SimTitle", wordWrap=True)
        self.bundle_id = QLabel(objectName="SimId")
        self.bundle_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.problems = QLabel(objectName="Problems", wordWrap=True)
        self.run_error = QLabel(objectName="RunError", wordWrap=True)  # a failed run: why, first
        self.run_error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.test = QLabel(objectName="TestSummary", wordWrap=True)

        self.fields: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for row, (label, _) in enumerate(SUMMARY):
            value = QLabel(wordWrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(QLabel(label, objectName="FieldName"), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self.fields[label] = value
        grid.setColumnStretch(1, 1)

        self.inputs = _table(["Name", "Value", "Units"])
        self.outputs = _table(["Name", "Value", "Units"])
        self.files = _table(["Sector", "File", "Size"])
        self.files.cellDoubleClicked.connect(self._open_file)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.inputs, "Inputs")
        self.tabs.addTab(self.outputs, "Outputs")
        self.tabs.addTab(self.files, "Files")
        self.tested = _table(["Descriptor", "Predicted", "Real", ""])
        self.tabs.addTab(self.tested, "Blind test")

        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_folder)
        close = QPushButton("✕", objectName="IconButton")
        close.clicked.connect(self.closed)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

        self.body = QWidget()
        box = QVBoxLayout(self.body)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(8)
        box.addWidget(QLabel("SIMULATION", objectName="KindLabel"))
        box.addLayout(head)
        box.addWidget(self.bundle_id)
        box.addWidget(open_folder, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.run_error)
        box.addWidget(self.problems)
        box.addWidget(self.test)
        box.addSpacing(6)
        box.addLayout(grid)
        box.addSpacing(10)
        box.addWidget(self.tabs, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.empty)
        outer.addWidget(self.body)
        self.body.hide()

    def show_record(self, record: dict[str, Any] | None) -> None:
        if record is None:
            self.body.hide()
            self.empty.show()
            return
        sim = record["simulation"]
        self.folder = Path(sim["source_path"]) if sim.get("source_path") else None
        self.title.setText(sim.get("title", ""))
        self.bundle_id.setText(sim.get("bundle_id", ""))
        self._show_error(record)
        problems = sim.get("problems") or []
        self.problems.setText("\n".join("⚠ " + p for p in problems))
        self.problems.setVisible(bool(problems))
        for label, key in SUMMARY:
            self.fields[label].setText(elapsed(sim) if key is None else str(sim.get(key) or ""))

        for table, rows in ((self.inputs, record["inputs"]), (self.outputs, record["outputs"])):
            table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                text, full = shown_value(row)
                value = QTableWidgetItem(text)
                value.setToolTip(full)
                name = QTableWidgetItem(pretty_name(row.get("name", "")))
                name.setToolTip(row.get("name", ""))
                table.setItem(i, 0, name)
                table.setItem(i, 1, value)
                table.setItem(i, 2, QTableWidgetItem(row.get("units", "")))
        self.files.setRowCount(len(record["files"]))
        for i, f in enumerate(record["files"]):
            self.files.setItem(i, 0, QTableWidgetItem(f.get("sector", "")))
            path = QTableWidgetItem(f.get("relpath", ""))
            path.setToolTip("Double-click to open")
            self.files.setItem(i, 1, path)
            self.files.setItem(i, 2, QTableWidgetItem(size_text(f.get("size", 0))))
        self.tabs.setTabText(0, f"Inputs  {len(record['inputs'])}")
        self.tabs.setTabText(1, f"Outputs  {len(record['outputs'])}")
        self.tabs.setTabText(2, f"Files  {len(record['files'])}")
        self._show_test(record.get("test"))
        self.empty.hide()
        self.body.show()

    def _show_error(self, record: dict[str, Any]) -> None:
        """A failed run's panel starts with why: its error and the end of its traceback."""
        sim = record["simulation"]
        if not sim.get("failed"):
            self.run_error.hide()
            return
        outputs = {row.get("name"): row for row in record["outputs"]}
        trace = shown_value(outputs["traceback"])[1] if "traceback" in outputs else ""
        tail = "\n".join(trace.strip().splitlines()[-8:])
        redone = record.get("redone_by") or []
        lines = [("This run failed, and was redone by " + ", ".join(redone) + "." if redone else "This run failed."),
                 sim.get("error") or f"Status: {sim.get('status')}"]
        if tail and tail != sim.get("error"):
            lines += ["", tail]
        self.run_error.setObjectName("RunRedone" if redone else "RunError")
        self.run_error.style().unpolish(self.run_error)
        self.run_error.style().polish(self.run_error)
        self.run_error.setText("\n".join(lines))
        self.run_error.show()

    def _show_test(self, test: dict | None) -> None:
        """How the blind prediction of this run went (blind.py): a summary, and every value."""
        text = test_summary(test)
        self.test.setText(text)
        self.test.setVisible(bool(text))
        lines = test.get("lines") if test else None
        if isinstance(lines, str):
            lines = json.loads(lines)
        lines = sorted(lines or [], key=lambda ln: (ln.get("right", True), pretty_name(ln["key"].partition(":")[2])))
        self.tested.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            units = f" {ln['units']}" if ln.get("units") else ""
            guess = ln.get("guess")
            predicted = (sig(guess, 5) if isinstance(guess, (int, float)) else str(guess)) + units
            if "low" in ln:
                predicted += f"  (90 %: {sig(ln['low'], 4)} – {sig(ln['high'], 4)})"
            real = ln.get("real")
            cells = [pretty_name(ln["key"].partition(":")[2]), predicted,
                     (sig(real, 5) if isinstance(real, (int, float)) else str(real)) + units,
                     ("✓" if ln.get("right") else "✗") + ("" if "inside" not in ln else " in range" if ln["inside"] else " outside range")]
            for column, text in enumerate(cells):
                self.tested.setItem(i, column, QTableWidgetItem(text))
        self.tabs.setTabVisible(3, bool(lines))
        if lines:
            self.tabs.setTabText(3, f"Blind test  {sum(1 for ln in lines if ln.get('right'))}/{len(lines)}")

    def _open_file(self, row: int, _column: int) -> None:
        if self.folder:
            relpath = self.files.item(row, 1).text()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder / relpath)))

    def _open_folder(self) -> None:
        if self.folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))


# ---------------------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    recalculated = Signal()  # the ghost calculator finished (it runs on its own thread)

    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.driver = graph.connect()
        self.watcher: Watcher | None = None
        self.current: str | None = None
        self._titles: dict[str, str] = {}
        self._models: dict[str, list[dict]] = {}  # every name a descriptor goes by -> its models (one per family)
        self._calc_lock = threading.Lock()
        self._calc_again = False
        self.recalculated.connect(self._data_changed)
        self.drawer_node: str | None = None  # the node the side panel is showing
        # campaigns whose ghosts are worked out from their own runs only (a view: nothing is stored)
        saved = prefs.store().value("isolated_campaigns", [])
        self.isolated: set[str] = set(saved) if isinstance(saved, list) else {saved} if saved else set()
        self._isolated_cache: dict[str, tuple[tuple, list[dict]]] = {}

        self.setWindowTitle("MILO")
        self.setWindowIcon(QIcon(str(ASSETS / "milo.ico")))
        self.resize(1440, 880)

        self.splash = Splash()
        self.splash.retry.connect(self.start_services)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.splash)
        self.stack.addWidget(self._build_main())
        self.setCentralWidget(self.stack)

        self.starter = Starter(self.driver)
        self.starter.progress.connect(self.splash.show_progress)
        self.starter.failed.connect(self.splash.show_failure)
        self.starter.ready.connect(self.on_ready)

    # ---- building

    def _build_main(self) -> QWidget:
        wordmark = QLabel("MILO", objectName="Wordmark")
        wordmark.setFont(theme.wordmark_font("wordmark"))
        self.search = QLineEdit(placeholderText="Search simulations")
        self.search.setFixedWidth(320)
        self.status = QLabel(objectName="Status")
        self.fit_button = fit = QPushButton("Fit")
        scan = QPushButton("Scan now")
        self.views: dict[str, QPushButton] = {}
        for view in ("Graph", "Ghosts"):
            chip = QPushButton(view, objectName="Chip", checkable=True, autoExclusive=True)
            chip.clicked.connect(lambda _=False, v=view: self.show_view(v))
            self.views[view] = chip
        self.views["Graph"].setChecked(True)
        scan.clicked.connect(lambda: self.watcher and self.watcher.scan_now())

        bar = QWidget(objectName="TopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 10, 18, 10)
        row.setSpacing(16)
        row.addWidget(wordmark)
        row.addSpacing(12)
        row.addWidget(self.views["Graph"])
        row.addWidget(self.views["Ghosts"])
        row.addSpacing(12)
        row.addWidget(self.search)
        row.addStretch()
        row.addWidget(self.status)
        row.addWidget(fit)
        row.addWidget(scan)

        self.descriptors = DescriptorPanel()
        self.descriptors.changed.connect(lambda _keys: self.redraw())
        self.descriptors.sims_changed.connect(self.redraw)
        self.descriptors.recolored.connect(self._recolored)

        self.types = TypeBar()
        self.types.changed.connect(self.redraw)

        self.canvas = GraphView()
        self.canvas.node_selected.connect(self._on_select)
        self.canvas.node_opened.connect(self._show_node)
        self.canvas.cleared.connect(self._hide_node)
        self.search.textChanged.connect(self.canvas.set_search)
        fit.clicked.connect(self.canvas.fit)

        self.detail = Detail()
        self.detail.closed.connect(lambda: self.canvas.select_node(None))
        self.info = NodeInfo()
        self.info.closed.connect(lambda: self.canvas.select_node(None))
        self.info.sim_clicked.connect(self._open_id)
        self.info.isolate_toggled.connect(self._toggle_isolate)
        self.drawer = Drawer(self.canvas, self.detail, self.info)

        middle = QWidget()
        column = QVBoxLayout(middle)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.types)
        column.addWidget(self.canvas, 1)

        # The descriptors float over the graph so nodes remain visible through the glass.
        self.canvas.set_sidebar(self.descriptors)

        self.formulas = FormulaView()
        self.formulas.recolored.connect(self._recolored)
        self.pages = QStackedWidget()
        self.pages.addWidget(middle)
        self.pages.addWidget(self.formulas)

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(bar)
        box.addWidget(self.pages, 1)
        return page

    def show_view(self, view: str) -> None:
        """Graph: the simulations and their nodes. Ghosts: the formula finder."""
        graph_view = view == "Graph"
        self.pages.setCurrentIndex(0 if graph_view else 1)
        for widget in (self.search, self.fit_button, self.status):
            widget.setVisible(graph_view)
        self.views[view].setChecked(True)

    # ---- lifecycle

    def start_services(self) -> None:
        self.splash.show_progress("Starting MILO…")
        self.stack.setCurrentWidget(self.splash)
        self.starter.start()

    def on_ready(self) -> None:
        self.reload()
        self.stack.setCurrentIndex(1)
        self.watcher = Watcher(self.driver, self.settings.drop_dir, self.settings.scan_seconds)
        self.watcher.ingested.connect(self.on_ingested)
        self.watcher.run_failed.connect(self.on_run_failed)
        self.watcher.removed.connect(self.on_removed)
        self.watcher.failed.connect(lambda folder, err: self.statusBar().showMessage(f"Could not read {folder}: {err}"))
        self.watcher.scanned.connect(self._on_scanned)
        self.watcher.start()
        self.statusBar().showMessage(f"Watching {self.settings.drop_dir}", 5000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.watcher:
            self.watcher.stop()
        self.driver.close()
        super().closeEvent(event)

    # ---- data

    def reload(self) -> None:
        """Re-read the descriptor list and the graph (after startup and after every ingest), then
        recalculate models, relationships and ghosts in the background and redraw when done."""
        self._data_changed()
        self._recalculate()

    def _data_changed(self) -> None:
        """The data or its calculation changed: refresh everything that shows it. (Ticking a
        descriptor only needs redraw().)"""
        self.descriptors.set_groups(graph.descriptor_groups(self.driver))
        self.descriptors.set_sims(graph.with_redos(graph.list_simulations(self.driver)))
        self.redraw()
        self.formulas.set_data(graph.descriptor_table(self.driver), graph.list_relationships(self.driver), self._titles)
        self.formulas.set_track(graph.list_tests(self.driver))

    def _recalculate(self) -> None:
        if not self._calc_lock.acquire(blocking=False):
            self._calc_again = True  # one is running: run once more when it ends, with the newest data
            return

        def work() -> None:
            try:
                while True:
                    self._calc_again = False
                    predict.refresh(self.driver, self.settings.ghosts)
                    if not self._calc_again:
                        break
            finally:
                self._calc_lock.release()
            self.recalculated.emit()

        threading.Thread(target=work, name="milo-ghosts", daemon=True).start()

    def redraw(self) -> None:
        """Build every node and link, then show only the kinds switched on in the type bar."""
        sims = graph.with_redos(graph.list_simulations(self.driver))
        self._titles = {s["bundle_id"]: str(s.get("title") or s["bundle_id"]) for s in sims}
        relationships = graph.list_relationships(self.driver)
        self._relationships = relationships
        predictions = graph.list_predictions(self.driver)
        self._models = {}
        for m in graph.list_models(self.driver):
            for alias in m.get("aliases") or [m["descriptor"]]:
                self._models.setdefault(alias, []).append(m)
        # The Sim Feed says which runs are on the graph; the rest, and their ghosts, are left off.
        shown_sims = {s["bundle_id"] for s in sims} - self.descriptors.hidden_sims
        nodes = [{"id": s["bundle_id"], "kind": "sim", "sim": s} for s in sims if s["bundle_id"] in shown_sims]
        links: list[dict] = []

        descriptor_ids: dict[str, list[str]] = {}
        for key in self.descriptors.active:
            color = self.descriptors.color_of(key)
            made = self._descriptor_nodes(key, color, shown_sims)
            if made:
                descriptor_ids[key] = [n["id"] for n, _ in made]
            for node, sim_ids in made:
                nodes.append(node)
                links += [{"a": node["id"], "b": s, "color": color} for s in sim_ids]

        # Relationships: every one is calculated; the ones shown are those between checked descriptors
        # (either side may be any of the names a merged descriptor goes by).
        for rel in relationships:
            ends = [[k for k in (rel.get(side + "_keys") or [rel.get(side)]) if k in descriptor_ids] for side in ("a", "b")]
            if not ends[0] or not ends[1]:
                continue
            rel_id = "rel:" + rel["id"]
            nodes.append({"id": rel_id, "kind": "relationship", "rel": rel})
            for key in ends[0] + ends[1]:
                links += [{"a": rel_id, "b": d, "color": theme.GRAPH["rel_edge"], "width": theme.GRAPH["rel_edge_width"],
                           "alpha": 200} for d in descriptor_ids[key]]

        campaign_by_sim = {s["bundle_id"]: graph.campaign_of(s) for s in sims}
        predictions = self._with_isolated(predictions, campaign_by_sim)
        ghost_rings: dict[str, list[str]] = {}
        for pred in predictions:
            if not set(pred["based_on"]) & shown_sims:
                continue  # its runs are unchecked in the Sim Feed
            pred_id = "pred:" + pred["id"]
            # a ghost of a campaign's runs belongs in the campaign's ring, faintly in its color
            homes = {campaign_by_sim.get(s) for s in pred["based_on"]}
            home = next(iter(homes)) if len(homes) == 1 else None
            if home:
                ghost_rings.setdefault(home, []).append(pred_id)
            nodes.append({"id": pred_id, "kind": "prediction", "pred": pred, "title": ghost_title(pred, self._titles),
                          "tint": campaign_color(home) if home else None})
            links += [{"a": pred_id, "b": s, "color": theme.GRAPH["ghost_line"], "style": "dashed", "alpha": 200}
                      for s in pred["based_on"]]

        counts = {kind: sum(n["kind"] == kind for n in nodes) for kind in ("sim", "descriptor", "relationship", "prediction")}
        self.types.set_counts(counts)
        shown = self.types.shown
        self.canvas.set_graph([n for n in nodes if n["kind"] in shown], links)
        # a spinning ring around each campaign's runs on screen, in the campaign's color
        rings: dict[str, list[str]] = {}
        for s in sims:
            name = graph.campaign_of(s)
            if name and s["bundle_id"] in shown_sims:
                rings.setdefault(name, []).append(s["bundle_id"])
        self.canvas.set_campaigns([{"name": n, "color": campaign_color(n), "sims": ids,
                                    "ghosts": ghost_rings.get(n, []) if "prediction" in shown else [],
                                    "spinning": n in self.isolated}
                                   for n, ids in rings.items()] if "sim" in shown else [])
        hints = []
        if "descriptor" in shown and not self.descriptors.active:
            hints.append("No descriptors checked: check fields in the list on the left")
        if "relationship" in shown and len(self.descriptors.active) == 1:
            hints.append("Check a second descriptor to see the relationships between them")
        broken = [s for s in sims if s["bundle_id"] in shown_sims and s.get("failed") and not s.get("redone")]
        if broken and "sim" in shown:
            hints.insert(0, f"{len(broken)} run{'s' if len(broken) != 1 else ''} failed (red "
                            "ring): double-click one to see why")
        if sims and not shown_sims:
            hints.append("Every run is unchecked in the Sim Feed")
        if not shown:
            hints.append("Every kind of node is switched off: turn some on in the Show bar")
        self.canvas.set_hint("   ·   ".join(hints))
        self.status.setText(f"{sum(counts[k] for k in shown)} nodes shown")

    def _recolored(self) -> None:
        """A campaign got a new color in one panel: show it in the other and on its ring."""
        self.descriptors.refresh_colors()
        self.formulas.refresh_colors()
        self.redraw()

    def _descriptor_nodes(self, key: str, color: str, shown_sims: set[str]) -> list[tuple[dict, list[str]]]:
        """One node per value of the group as it reads on screen ("Density = 1 g/cm^3"), each with
        the simulations that have it. Values that only differ past the shown digits (1.0000009 and
        1.0000011) read the same, so they share a node; the exact values stay in its details."""
        name = key.partition(":")[2]
        shown: dict[str, tuple[list[str], list[str]]] = {}  # label -> (sim ids, exact values)
        for value_json, units, bundle_id in graph.descriptor_links(self.driver, key):
            if bundle_id not in shown_sims:
                continue
            label = f"{pretty_name(name)} = {quantity(value_json, units)}"
            sims, exact = shown.setdefault(label, ([], []))
            sims.append(bundle_id)
            raw = f"{value_json} {units}" if units else value_json
            if raw not in exact:
                exact.append(raw)
        made = []
        for label, (sim_ids, exact) in shown.items():
            count = len(sim_ids)
            tip = f"{label}\n{count} simulation{'s' if count != 1 else ''}"
            node = {"id": f"{key}={label}", "kind": "descriptor", "group": key, "label": label,
                    "color": color, "sims": sim_ids, "tooltip": tip, "exact": exact}
            made.append((node, sim_ids))
        return made

    def _on_select(self, data: dict) -> None:
        """Selecting (or dragging) a node never opens the panel; selecting a different node than
        the one the panel shows closes it."""
        if self.drawer.is_open and data["id"] != self.drawer_node:
            self._hide_node()

    def _open_id(self, key: str) -> None:
        """A linked simulation clicked in the panel: select it and show it straight away."""
        node = self.canvas.nodes.get(key)
        if node is None:
            return
        self.drawer_node = key
        self.canvas.select_node(key)
        self._show_node(node.data_)

    def _show_node(self, data: dict) -> None:
        self.drawer_node = data["id"]
        kind = data["kind"]
        if kind == "sim":
            self.current = data["id"]
            self.detail.show_record(graph.get_simulation(self.driver, data["id"]))
            self.drawer.show_page(0)
            self._keep_in_view(data["id"])
            return
        self.current = None
        titles = {"descriptor": lambda: data["label"], "relationship": lambda: data["rel"].get("name") or "Relationship",
                  "prediction": lambda: data.get("title") or data["pred"].get("title") or "Prediction"}
        linked = {"descriptor": lambda: data["sims"],
                  "relationship": lambda: [p[0] for p in loads(data["rel"].get("points"), [])],
                  "prediction": lambda: data["pred"].get("based_on") or []}
        sims = [(s, self._titles.get(s, s)) for s in linked[kind]()]
        models = self._models.get(data.get("group"), []) if kind == "descriptor" else []
        summary = None
        if kind == "prediction":
            pred = data["pred"]
            base = pred.get("base") or (pred.get("based_on") or [None])[0]
            row = next((r for r in graph.descriptor_table(self.driver) if r["id"] == base), None)
            summary = prediction_summary(pred, row["values"] if row else None, short_title(self._titles.get(base, ""), 28))
            home = {graph.campaign_of(s) for s in graph.list_simulations(self.driver) if s["bundle_id"] in (pred.get("based_on") or [])}
            campaign = next(iter(home)) if len(home) == 1 else None
            if campaign and pred.get("source") in ("calculation", None):
                summary["isolate"] = {"campaign": campaign, "on": campaign in self.isolated}
        elif kind == "relationship":
            summary = relationship_summary(data["rel"])
        elif kind == "descriptor":
            summary = descriptor_summary(data, models, getattr(self, "_relationships", []))
        math = {"prediction": lambda: prediction_math(data["pred"], self._titles),
                "descriptor": lambda: descriptor_math(data, models, self._titles),
                "relationship": lambda: relationship_math(data["rel"], self._titles)}.get(
            kind, lambda: node_rows(data, models, self._titles))()
        self.info.show_node(kind, titles[kind](), math, sims, summary)
        self.drawer.show_page(1)
        self._keep_in_view(data["id"])

    # ---- isolated campaigns

    def _with_isolated(self, predictions: list[dict], campaign_by_sim: dict[str, str | None]) -> list[dict]:
        """For each isolated campaign, its calculated ghosts are replaced by ones worked out from its own
        runs only (predict.calculate over just those runs; kept in memory, never stored)."""
        if not self.isolated:
            return predictions
        table = None
        for name in sorted(self.isolated):
            members = {b for b, c in campaign_by_sim.items() if c == name}
            if not members:
                continue
            predictions = [p for p in predictions
                           if not (p.get("source") == "calculation" and set(p.get("based_on") or []) <= members)]
            table = table if table is not None else graph.descriptor_table(self.driver)
            rows = [r for r in table if r["id"] in members]
            key = tuple(sorted((r["id"], r.get("fingerprint")) for r in rows))
            cached = self._isolated_cache.get(name)
            if cached is None or cached[0] != key:
                ghosts = predict.calculate(rows, self.settings.ghosts)["ghosts"] if rows else []
                cached = (key, [g | {"id": "iso-" + g["id"], "source": "calculation", "isolated": name} for g in ghosts])
                self._isolated_cache[name] = cached
            predictions = predictions + cached[1]
        return predictions

    def _toggle_isolate(self, campaign: str, on: bool) -> None:
        """Isolate a campaign (or stop): redraw, and keep the panel on the same ghost (same run, same knob)."""
        node = self.canvas.nodes.get(self.drawer_node or "")
        pred = node.data_.get("pred") if node is not None else None
        (self.isolated.add if on else self.isolated.discard)(campaign)
        prefs.store().setValue("isolated_campaigns", sorted(self.isolated))
        self.redraw()
        if pred is None:
            return
        # the same run and knob if it still has a ghost, else another ghost of the same run, else of the campaign
        ghosts = [(k, n) for k, n in self.canvas.nodes.items() if n.data_.get("pred")]
        for match in (lambda p: p.get("base") == pred.get("base") and p.get("changed") == pred.get("changed"),
                      lambda p: p.get("base") == pred.get("base"),
                      lambda p: (p.get("isolated") == campaign) if on else False):
            for key, other in ghosts:
                if match(other.data_["pred"]):
                    self.canvas.select_node(key)
                    self._show_node(other.data_)
                    return
        self._hide_node()

    def _keep_in_view(self, key: str) -> None:
        """Once the drawer has slid in, make sure the tapped node is not hidden under it."""
        QTimer.singleShot(theme.SIZES["drawer_ms"], lambda: self.canvas.reveal(key, theme.SIZES["drawer"]))

    def _hide_node(self) -> None:
        self.current = None
        self.drawer_node = None
        self.drawer.close_drawer()

    def on_run_failed(self, bundle_id: str, title: str, error: str) -> None:
        """Say so straight away: a run that arrives failed."""
        self.statusBar().showMessage(f"{title} failed: {error}", 20000)

    def on_removed(self, titles: list[str]) -> None:
        """Runs whose folders were deleted from the drop folder have left the graph."""
        self.reload()
        self.statusBar().showMessage("Removed (folder deleted from the drop folder): " + ", ".join(titles), 12000)

    def on_ingested(self, bundle_id: str, title: str) -> None:
        self.reload()
        if bundle_id == self.current:
            self.detail.show_record(graph.get_simulation(self.driver, bundle_id))
        self.statusBar().showMessage(f"Added: {title}", 8000)

    def _on_scanned(self, _count: int) -> None:
        if not self.settings.drop_dir.is_dir():
            self.statusBar().showMessage(f"Drop folder not found: {self.settings.drop_dir}")


def delayed_start(window: MainWindow) -> None:
    """Show the window first, then start the services, so the starting screen paints immediately."""
    QTimer.singleShot(0, window.start_services)
