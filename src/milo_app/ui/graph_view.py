"""The graph canvas. Four kinds of node, each drawn its own way:

    sim           a simulation that ran: black circle, short name underneath
    descriptor    groups simulations: a value (Forcite, Blend) or a quantity (Tg): white pill
    relationship  between two descriptors (Tg <-> water uptake): black diamond
    prediction    a run that has not happened yet, from the ML: dashed ghost circle

The layout is a small force simulation: nodes push each other apart, links pull a descriptor
toward its simulations, and a light pull keeps everything near the middle. It runs for a few
seconds after anything changes and then rests. Drag a node to move it, drag the background to
pan, wheel to zoom. Clicking a node selects it and a little arrow fades in where you clicked (kept
inside the node), then fades away. Double-clicking anywhere on a node opens the side panel.
"""
from __future__ import annotations

import math
import random
from typing import Any

from PySide6.QtCore import (
    QEasingCurve, QLineF, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation, QRectF,
    QSequentialAnimationGroup, Qt, QTimer, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsScene, QGraphicsView, QLabel,
)

from milo_app.ui import theme
from milo_app.ui.format import elapsed, short_title

G = theme.GRAPH

# Force layout. Forces are scaled by `alpha`, which cools each tick; the layout rests below ALPHA_MIN.
REPULSION = 42000.0
SPRING = 0.035
SPRING_LENGTH = 170.0
GRAVITY = 0.004
DAMPING = 0.82
MAX_STEP = 30.0
COOLING = 0.985
ALPHA_MIN = 0.01
TICK_MS = 16


# ---------------------------------------------------------------------------- items


class Node(QGraphicsObject):
    def __init__(self, key: str, canvas: "GraphView") -> None:
        super().__init__()
        self.key, self.canvas = key, canvas
        self.vx = self.vy = 0.0
        self.held = False  # being dragged: the layout leaves it where the mouse puts it
        self.edges: list[Edge] = []
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(1)
        self.handle = OpenHandle(self)

    def handle_spot(self, click: QPointF) -> QPointF:
        """Where the arrow goes for a click at `click` (node coordinates): nudged a little up and to
        the right of the pointer, then pulled back inside the node's shape (each kind says how)."""
        return self.inside(click + QPointF(G["handle_nudge"], -G["handle_nudge"]))

    def inside(self, p: QPointF) -> QPointF:
        return QPointF(0, 0)

    def itemChange(self, change, value):  # noqa: N802 - Qt's name
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and not value:
            self.handle.vanish()
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.adjust()
            if self.held:
                self.canvas.reheat(0.3)
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.held = True
        self.handle.pop(self.handle_spot(event.pos()))
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Anywhere on the node counts: the whole node is the arrow's target."""
        event.accept()
        self.canvas.open_node(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.held = False
        super().mouseReleaseEvent(event)


class OpenHandle(QGraphicsObject):
    """The little arrow that fades in where a node was clicked and fades away again: a hint that a
    double-click opens the side panel. It takes no clicks itself (the node underneath does), so it
    never gets in the way of a drag."""

    def __init__(self, node: Node) -> None:
        super().__init__(node)
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setOpacity(0.0)
        self.hide()
        come = QParallelAnimationGroup(self)
        for prop, start, end in ((b"opacity", 0.0, 1.0), (b"scale", 0.6, 1.0)):
            anim = QPropertyAnimation(self, prop, self)
            anim.setDuration(G["handle_in_ms"])
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            come.addAnimation(anim)
        go = QPropertyAnimation(self, b"opacity", self)
        go.setDuration(G["handle_out_ms"])
        go.setStartValue(1.0)
        go.setEndValue(0.0)
        go.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.life = QSequentialAnimationGroup(self)
        self.life.addAnimation(come)
        self.life.addPause(G["handle_stay_ms"])
        self.life.addAnimation(go)
        self.life.finished.connect(self.hide)

    def pop(self, spot: QPointF) -> None:
        """Come in at `spot`, stay a moment, fade away."""
        self.life.stop()
        self.setPos(spot)
        self.show()
        self.life.start()

    def vanish(self) -> None:
        self.life.stop()
        self.setOpacity(0.0)
        self.hide()

    def boundingRect(self) -> QRectF:  # noqa: N802
        r = G["handle_size"] / 2 + 1
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        r = G["handle_size"] / 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(G["handle_fill"]))
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.setPen(QPen(QColor(G["handle_ink"]), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline([QPointF(-2, -4.5), QPointF(2.5, 0), QPointF(-2, 4.5)])  # a small ›


class SimNode(Node):
    def __init__(self, data: dict[str, Any], canvas: "GraphView") -> None:
        super().__init__(data["id"], canvas)
        self.r = G["sim_size"] / 2
        self.label_font = theme.font(G["sim_label_px"], bold=True)
        self.set_data(data)
        self.setZValue(2)

    def set_data(self, data: dict[str, Any]) -> None:
        self.data_ = data
        sim = data["sim"]
        self.sim = sim
        self.title = str(sim.get("title") or sim["bundle_id"])
        metrics = QFontMetrics(self.label_font)
        self.label = metrics.elidedText(short_title(self.title), Qt.TextElideMode.ElideRight, G["sim_label_max"])
        self.label_w = metrics.horizontalAdvance(self.label) + 4
        details = " · ".join(p for p in (str(sim.get("module") or ""), elapsed(sim)) if p)
        self.setToolTip("\n".join(p for p in (self.title, details, sim["bundle_id"]) if p))
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = max(self.r + 5, self.label_w / 2)
        return QRectF(-half, -self.r - 5, 2 * half, 2 * self.r + 10 + G["sim_label_px"] + 6)

    def inside(self, p: QPointF) -> QPointF:
        return _in_circle(p, self.r)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(G["sim_fill"]))
        painter.drawEllipse(QPointF(0, 0), self.r, self.r)
        painter.setFont(self.label_font)
        painter.setPen(QColor(theme.COLORS["ink"]))
        painter.drawText(QRectF(-self.label_w / 2, self.r + 6, self.label_w, G["sim_label_px"] + 6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.label)


class DescriptorNode(Node):
    def __init__(self, data: dict[str, Any], canvas: "GraphView") -> None:
        super().__init__(data["id"], canvas)
        self.value_font = theme.font(G["desc_value_px"], bold=True)
        self.set_data(data)

    def set_data(self, desc: dict[str, Any]) -> None:
        self.data_ = self.desc = desc
        self.color = QColor(desc["color"])
        self.value = desc["label"]
        wide = QFontMetrics(self.value_font).horizontalAdvance(self.value)
        self.prepareGeometryChange()
        self.w = min(G["desc_max_width"], wide + 40)  # 24 px dot side + 12 px end + slack
        self.h = G["desc_height"]
        self.setToolTip(desc["tooltip"])
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-self.w / 2 - 4, -self.h / 2 - 4, self.w + 8, self.h + 8)

    def inside(self, p: QPointF) -> QPointF:
        """Along the pill's straight middle, centred on its height."""
        room_x = max(self.w / 2 - self.h / 2, 0)
        room_y = max(self.h / 2 - G["handle_size"] / 2, 0)
        return QPointF(min(max(p.x(), -room_x), room_x), min(max(p.y(), -room_y), room_y))

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        rect = QRectF(-self.w / 2, -self.h / 2, self.w, self.h)
        radius = self.h / 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.color, 2))
        painter.setBrush(QColor(G["desc_fill"]))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(QPointF(rect.left() + 13, 0), 4, 4)

        text = rect.adjusted(24, 0, -12, 0)
        painter.setFont(self.value_font)
        painter.setPen(QColor(G["desc_ink"]))
        value = QFontMetrics(self.value_font).elidedText(self.value, Qt.TextElideMode.ElideRight, int(text.width()))
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)


class RelationshipNode(Node):
    def __init__(self, data: dict[str, Any], canvas: "GraphView") -> None:
        super().__init__(data["id"], canvas)
        self.label_font = theme.font(G["rel_label_px"], bold=True)
        self.set_data(data)
        self.setZValue(3)

    def set_data(self, data: dict[str, Any]) -> None:
        rel = data["rel"]
        self.data_ = data
        self.name = str(rel.get("name") or "relationship")
        sure = rel.get("confidence")
        bits = ([f"r {rel['r']:+.2f}"] if isinstance(rel.get("r"), (int, float)) else []) + \
               ([f"{sure:.0%} sure"] if isinstance(sure, (int, float)) else [])
        self.label = " · ".join(bits) or "relationship"
        self.label_w = min(200, QFontMetrics(self.label_font).horizontalAdvance(self.label) + 4)
        self.setToolTip(self.name + (f"\n{self.label}" if bits else ""))
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        s = G["rel_size"] / 2 + 4
        w = max(s, self.label_w / 2)
        return QRectF(-w, -s, 2 * w, 2 * s + G["rel_label_px"] + 6)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        s = G["rel_size"] / 2
        diamond = QPolygonF([QPointF(0, -s), QPointF(s, 0), QPointF(0, s), QPointF(-s, 0)])
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(G["rel_fill"]))
        painter.drawPolygon(diamond)
        painter.setFont(self.label_font)
        painter.setPen(QColor(theme.COLORS["ink"]))
        text = QFontMetrics(self.label_font).elidedText(self.label, Qt.TextElideMode.ElideRight, int(self.label_w))
        painter.drawText(QRectF(-self.label_w / 2, s + 3, self.label_w, G["rel_label_px"] + 4),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)


class PredictionNode(Node):
    """A ghost of a simulation circle: the run the ML suggests, not yet made."""

    def __init__(self, data: dict[str, Any], canvas: "GraphView") -> None:
        super().__init__(data["id"], canvas)
        self.r = G["sim_size"] / 2
        self.label_font = theme.font(G["sim_label_px"], bold=True)
        self.set_data(data)
        self.setZValue(2)

    def set_data(self, data: dict[str, Any]) -> None:
        pred = data["pred"]
        self.data_ = data
        self.title = str(pred.get("title") or "prediction")
        metrics = QFontMetrics(self.label_font)
        sure = pred.get("confidence")
        if pred.get("source") == "verified" and pred.get("values"):
            label = f"Verified · {pred.get('right')}/{pred.get('values')} right"  # it was really run
        elif isinstance(sure, (int, float)):
            label = f"Ghost · {sure:.0%}" if sure >= 0.01 else f"Ghost · {sure:.1%}"  # 0.4 % must not read "0 %"
        else:
            label = "Ghost"
        self.label = metrics.elidedText(label, Qt.TextElideMode.ElideRight, G["sim_label_max"])
        self.label_w = metrics.horizontalAdvance(self.label) + 4
        made = "reasoned by the MCP" if pred.get("source") == "mcp" else "calculated"
        sure_text = f"{sure:.1%} confidence" if isinstance(sure, (int, float)) else "no confidence given"
        self.setToolTip(f"{self.title}\nPrediction, {made}, {sure_text}")
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = max(self.r + 5, self.label_w / 2)
        return QRectF(-half, -self.r - 5, 2 * half, 2 * self.r + 10 + G["sim_label_px"] + 6)

    def inside(self, p: QPointF) -> QPointF:
        return _in_circle(p, self.r)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(G["ghost_line"]), 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(G["ghost_fill"]))
        painter.drawEllipse(QPointF(0, 0), self.r - 0.75, self.r - 0.75)
        painter.setFont(self.label_font)
        painter.setPen(QColor(G["ghost_ink"]))
        painter.drawText(QRectF(-self.label_w / 2, self.r + 6, self.label_w, G["sim_label_px"] + 6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.label)


KINDS = {"sim": SimNode, "descriptor": DescriptorNode, "relationship": RelationshipNode, "prediction": PredictionNode}


def _in_circle(p: QPointF, radius: float) -> QPointF:
    """`p`, pulled back so an arrow centred there stays inside a circle of `radius`."""
    room = max(radius - G["handle_size"] / 2 - 1, 0)
    distance = math.hypot(p.x(), p.y())
    return p if distance <= room else QPointF(p.x() * room / distance, p.y() * room / distance)


class Edge(QGraphicsLineItem):
    STYLES = {"solid": Qt.PenStyle.SolidLine, "dashed": Qt.PenStyle.DashLine}

    def __init__(self, a: Node, b: Node, color: str, style: str = "solid", width: float | None = None,
                 alpha: int | None = None) -> None:
        super().__init__()
        self.a, self.b = a, b
        pen_color = QColor(color)
        pen_color.setAlpha(G["edge_alpha"] if alpha is None else alpha)
        self.setPen(QPen(pen_color, width or G["edge_width"], self.STYLES[style]))
        self.setZValue(0)
        a.edges.append(self)
        b.edges.append(self)
        self.adjust()

    def adjust(self) -> None:
        self.setLine(QLineF(self.a.pos(), self.b.pos()))

    def detach(self) -> None:
        for node in (self.a, self.b):
            if self in node.edges:
                node.edges.remove(self)


# ---------------------------------------------------------------------------- canvas


class GraphView(QGraphicsView):
    node_selected = Signal(object)  # a node was clicked (selected): its data {"id", "kind", ...}
    node_opened = Signal(object)  # its open-handle was held: show it in the side panel
    cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Graph")
        self.graph_scene = QGraphicsScene(self)
        self.graph_scene.setSceneRect(-50000, -50000, 100000, 100000)
        self.graph_scene.setBackgroundBrush(QBrush(QColor(G["canvas"])))
        self.graph_scene.selectionChanged.connect(self._on_selection)
        self.setScene(self.graph_scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.search = ""
        self.alpha = 0.0
        self._fit_pending = True
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

        self.hint = QLabel(self, objectName="GraphHint", alignment=Qt.AlignmentFlag.AlignCenter)
        self.hint.hide()

    # ---- data

    def set_graph(self, nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
        """Show exactly these. Each node is {"id", "kind", ...}; each link {"a", "b", "color", "style"}.
        Nodes already on the canvas keep their place; new ones appear next to what they link to.
        Links whose ends are not both shown are skipped."""
        wanted = {n["id"]: n for n in nodes}
        for key in [k for k, node in self.nodes.items()
                    if k not in wanted or not isinstance(node, KINDS[wanted[k]["kind"]])]:
            self.graph_scene.removeItem(self.nodes.pop(key))
        for edge in self.edges:
            edge.detach()
            self.graph_scene.removeItem(edge)
        self.edges = []

        neighbours: dict[str, list[str]] = {}
        for link in links:
            neighbours.setdefault(link["a"], []).append(link["b"])
            neighbours.setdefault(link["b"], []).append(link["a"])
        spread = 140 * math.sqrt(max(len(nodes), 1))
        # Simulations first, so everything else can be placed next to the blocks it links to.
        for data in sorted(nodes, key=lambda n: n["kind"] != "sim"):
            node = self.nodes.get(data["id"])
            if node is not None:
                node.set_data(data)
                continue
            node = KINDS[data["kind"]](data, self)
            placed = [self.nodes[i].pos() for i in neighbours.get(data["id"], []) if i in self.nodes]
            if placed:
                x = sum(p.x() for p in placed) / len(placed) + random.uniform(-60, 60)
                y = sum(p.y() for p in placed) / len(placed) + random.uniform(-60, 60)
            else:
                x, y = random.uniform(-spread, spread), random.uniform(-spread, spread)
            node.setPos(x, y)
            self._add(node)
        for link in links:
            if link["a"] in self.nodes and link["b"] in self.nodes:
                edge = Edge(self.nodes[link["a"]], self.nodes[link["b"]], link["color"], link.get("style", "solid"),
                            link.get("width"), link.get("alpha"))
                self.graph_scene.addItem(edge)
                self.edges.append(edge)
        self._apply_emphasis()
        self.reheat(1.0)

    def open_node(self, node: Node) -> None:
        self.node_opened.emit(node.data_)

    def set_hint(self, text: str) -> None:
        """A line of help floating at the top of the canvas; empty hides it."""
        self.hint.setText(text)
        self.hint.setVisible(bool(text))
        self._place_hint()

    def _place_hint(self) -> None:
        self.hint.adjustSize()
        self.hint.move((self.width() - self.hint.width()) // 2, 16)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_hint()

    def _add(self, node: Node) -> None:
        self.nodes[node.key] = node
        self.graph_scene.addItem(node)

    def select_node(self, key: str | None) -> None:
        self.graph_scene.clearSelection()
        if key in self.nodes:
            self.nodes[key].setSelected(True)

    def reveal(self, key: str, covered_right: int = 0) -> None:
        """Pan just enough to bring a node into view, treating the right `covered_right` pixels
        (the side panel) as hidden."""
        node = self.nodes.get(key)
        if node is None:
            return
        box = self.mapFromScene(node.sceneBoundingRect()).boundingRect()
        margin, view = 40, self.viewport().rect()
        right = view.width() - covered_right - margin
        dx = box.right() - right if box.right() > right else min(0, box.left() - margin)
        dy = box.bottom() - (view.height() - margin) if box.bottom() > view.height() - margin else min(0, box.top() - margin)
        if dx or dy:
            self.centerOn(self.mapToScene(view.center() + QPoint(int(dx), int(dy))))

    def set_search(self, text: str) -> None:
        self.search = text.strip().lower()
        self._apply_emphasis()

    # ---- emphasis: search only

    def _on_selection(self) -> None:
        selected = [i for i in self.graph_scene.selectedItems() if isinstance(i, Node)]
        self._apply_emphasis()
        if selected:
            self.node_selected.emit(selected[0].data_)
        else:
            self.cleared.emit()

    def _apply_emphasis(self) -> None:
        """Searching fades the sims that do not match (and their links). Selecting fades nothing."""
        if self.search:
            lit = {k for k, n in self.nodes.items() if self._node_matches(n)}
            for key in list(lit):
                for edge in self.nodes[key].edges:
                    lit |= {edge.a.key, edge.b.key}
        else:
            lit = None
        for key, node in self.nodes.items():
            node.setOpacity(1.0 if lit is None or key in lit else G["dim"])
        for edge in self.edges:
            on = lit is None or (edge.a.key in lit and edge.b.key in lit)
            edge.setOpacity(1.0 if on else G["dim"] / 2)

    def _node_matches(self, node: Node) -> bool:
        """The search box finds simulations, descriptor values, relationships and ghosts by name."""
        if isinstance(node, SimNode):
            return self._matches(node.sim)
        text = {DescriptorNode: lambda: node.value, RelationshipNode: lambda: node.name,
                PredictionNode: lambda: node.title}.get(type(node), lambda: "")()
        return self.search in text.lower()

    def _matches(self, sim: dict[str, Any]) -> bool:
        return any(self.search in str(sim.get(k) or "").lower() for k in ("title", "module", "task", "bundle_id", "status"))

    # ---- layout

    def reheat(self, alpha: float) -> None:
        self.alpha = max(self.alpha, alpha)
        if not self.timer.isActive():
            self.timer.start()

    def _tick(self) -> None:
        nodes = list(self.nodes.values())
        n = len(nodes)
        if n == 0 or self.alpha < ALPHA_MIN:
            self.timer.stop()
            return
        index = {node.key: i for i, node in enumerate(nodes)}
        xs = [node.x() for node in nodes]
        ys = [node.y() for node in nodes]
        fx, fy = [0.0] * n, [0.0] * n
        a = self.alpha

        for i in range(n):
            xi, yi = xs[i], ys[i]
            for j in range(i + 1, n):
                dx, dy = xi - xs[j], yi - ys[j]
                d2 = dx * dx + dy * dy
                if d2 > 1_440_000:  # beyond 1200 px the push is negligible
                    continue
                if d2 < 1.0:
                    dx, dy, d2 = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
                d = math.sqrt(d2)
                f = REPULSION / d2
                px, py = f * dx / d, f * dy / d
                fx[i] += px
                fy[i] += py
                fx[j] -= px
                fy[j] -= py
        for edge in self.edges:
            i, j = index[edge.a.key], index[edge.b.key]
            dx, dy = xs[j] - xs[i], ys[j] - ys[i]
            d = math.hypot(dx, dy) or 1.0
            f = SPRING * (d - SPRING_LENGTH)
            px, py = f * dx / d, f * dy / d
            fx[i] += px
            fy[i] += py
            fx[j] -= px
            fy[j] -= py

        for i, node in enumerate(nodes):
            if node.held:
                node.vx = node.vy = 0.0
                continue
            node.vx = (node.vx + a * (fx[i] - GRAVITY * xs[i])) * DAMPING
            node.vy = (node.vy + a * (fy[i] - GRAVITY * ys[i])) * DAMPING
            step = math.hypot(node.vx, node.vy)
            if step > MAX_STEP:
                node.vx, node.vy = node.vx * MAX_STEP / step, node.vy * MAX_STEP / step
            node.setPos(xs[i] + node.vx, ys[i] + node.vy)

        self.alpha *= COOLING
        if self._fit_pending and self.alpha < 0.25:
            self._fit_pending = False
            self.fit()

    # ---- view

    def fit(self) -> None:
        if not self.nodes:
            return
        rect = self.graph_scene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.4:  # never blow a small graph up past 140 %
            self.resetTransform()
            self.scale(1.4, 1.4)
            self.centerOn(rect.center())

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.0015 ** event.angleDelta().y()
        zoom = self.transform().m11() * factor
        if 0.08 < zoom < 4:
            self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.itemAt(event.position().toPoint()) is None:
            self.graph_scene.clearSelection()
        super().mousePressEvent(event)
