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

A campaign with runs on screen gets a dotted ring around them and their ghosts, in its color; it
turns slowly while the campaign is isolated (its ghosts worked out from its own runs only). Grab the
ring to move all of the campaign's runs at once.
"""
from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

from PySide6.QtCore import (
    QEasingCurve, QLineF, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation, QRectF,
    QSequentialAnimationGroup, Qt, QTimer, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsScene, QGraphicsView, QLabel,
)

from milo_app.ui import layout, theme
from milo_app.ui.format import elapsed, ghost_change, short_title

G = theme.GRAPH



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
            if not self.canvas.ticking:  # a tick moves every link once, after all the nodes
                for edge in self.edges:
                    edge.adjust()
            if self.held:
                self.canvas.reheat(0.3)
                for ring in self.canvas.rings.values():
                    if self in ring.sims:
                        ring.fit()
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
        self.failed, self.redone = bool(sim.get("failed")), bool(sim.get("redone"))
        trouble = ("Failed, then redone by a later run" if self.redone else
                   "FAILED: " + (sim.get("error") or f"status {sim.get('status')!r}") if self.failed else "")
        self.setToolTip("\n".join(p for p in (self.title, details, trouble, sim["bundle_id"]) if p))
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
        if self.failed:
            # a red ring and a "!" badge; grey and dashed, with no badge, once a later run redid it
            color = QColor(theme.COLORS["redone" if self.redone else "fail"])
            pen = QPen(color, G["fail_ring_width"])
            if self.redone:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), self.r + 3, self.r + 3)
            if not self.redone:
                b = G["fail_badge"] / 2
                spot = QPointF(self.r * 0.74, -self.r * 0.74)
                painter.setPen(QPen(QColor(G["canvas"]), 1.5))
                painter.setBrush(color)
                painter.drawEllipse(spot, b, b)
                painter.setPen(QColor("#ffffff"))
                painter.setFont(theme.font(G["fail_badge"] - 4, bold=True))
                painter.drawText(QRectF(spot.x() - b, spot.y() - b, 2 * b, 2 * b), Qt.AlignmentFlag.AlignCenter, "!")
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
        # as it fits between the dot and the end (the text box in paint(): 24 px in, 12 px from the end)
        self.shown = QFontMetrics(self.value_font).elidedText(self.value, Qt.TextElideMode.ElideRight, int(self.w - 36))
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
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.shown)


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
        self.shown = QFontMetrics(self.label_font).elidedText(self.label, Qt.TextElideMode.ElideRight, int(self.label_w))
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
        painter.drawText(QRectF(-self.label_w / 2, s + 3, self.label_w, G["rel_label_px"] + 4),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.shown)


class PredictionNode(Node):
    """A ghost of a simulation circle: the run the ML suggests, not yet made."""

    def __init__(self, data: dict[str, Any], canvas: "GraphView") -> None:
        super().__init__(data["id"], canvas)
        self.r = G["sim_size"] / 2
        self.label_font = theme.font(G["sim_label_px"], bold=True)
        self.set_data(data)
        self.setZValue(2)

    def set_data(self, data: dict[str, Any]) -> None:
        """Under the ghost: what it changes, in shorthand ("PLA frac 0.53→0.41"), then how sure it is,
        colored from red (unsure) to green (sure). A campaign's ghosts are tinted with its color."""
        pred = data["pred"]
        self.data_ = data
        self.title = data.get("title") or str(pred.get("title") or "prediction")
        self.tint = QColor(data["tint"]) if data.get("tint") else None
        sure = pred.get("confidence")
        if pred.get("source") == "verified" and pred.get("values"):
            self.sure_text = f"Verified · {pred.get('right')}/{pred.get('values')} right"  # it was really run
            share = pred["right"] / pred["values"] if isinstance(pred.get("right"), (int, float)) else None
        elif isinstance(sure, (int, float)):
            self.sure_text = f"{sure:.0%}" if sure >= 0.01 else f"{sure:.1%}"  # 0.4 % must not read "0 %"
            share = sure
        else:
            self.sure_text, share = "Ghost", None
        self.sure_color = QColor(theme.confidence_color(share) if share is not None else G["ghost_ink"])
        change, _ = ghost_change(pred)
        self.change_font = theme.font(G["ghost_change_px"])
        self.change = QFontMetrics(self.change_font).elidedText(change, Qt.TextElideMode.ElideRight, G["sim_label_max"])
        self.sure_w = QFontMetrics(self.label_font).horizontalAdvance(self.sure_text) + 4
        self.change_w = QFontMetrics(self.change_font).horizontalAdvance(self.change) + 4
        self.label_w = max(self.sure_w, self.change_w)
        made = "reasoned by the MCP" if pred.get("source") == "mcp" else "calculated"
        sure_line = f"{sure:.1%} confidence" if isinstance(sure, (int, float)) else "no confidence given"
        self.setToolTip(f"{self.title}\nPrediction, {made}, {sure_line}")
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = max(self.r + 5, self.label_w / 2)
        lines = G["ghost_change_px"] + 4 + G["sim_label_px"] + 6
        return QRectF(-half, -self.r - 5, 2 * half, 2 * self.r + 10 + lines)

    def inside(self, p: QPointF) -> QPointF:
        return _in_circle(p, self.r)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        line, fill = QColor(G["ghost_line"]), QColor(G["ghost_fill"])
        if self.tint is not None:  # faintly its campaign's color
            line, fill = QColor(self.tint), QColor(self.tint)
            line.setAlpha(G["ghost_tint_line"])
            fill.setAlpha(G["ghost_tint_fill"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(G["ghost_fill"]))  # a white disc under the tint, so links do not show through
            painter.drawEllipse(QPointF(0, 0), self.r - 0.75, self.r - 0.75)
        painter.setPen(QPen(line, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(0, 0), self.r - 0.75, self.r - 0.75)
        top = self.r + 5
        if self.change:
            painter.setFont(self.change_font)
            painter.setPen(QColor(G["ghost_ink"]))
            painter.drawText(QRectF(-self.change_w / 2, top, self.change_w, G["ghost_change_px"] + 4),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.change)
            top += G["ghost_change_px"] + 4
        painter.setFont(self.label_font)
        painter.setPen(self.sure_color)
        painter.drawText(QRectF(-self.sure_w / 2, top, self.sure_w, G["sim_label_px"] + 6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.sure_text)


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


class CampaignRing(QGraphicsObject):
    """A dotted ring around a campaign's runs on screen, in the campaign's color, turning slowly. Only
    the ring itself takes the mouse (a band along it): grab it to move every run of the campaign at
    once; inside it, nodes and the background work as usual."""

    def __init__(self, name: str, color: str, sims: list[Node], canvas: "GraphView", spinning: bool = False) -> None:
        """sims: the campaign's runs on screen and their ghosts: everything the ring holds and moves.
        spinning: the campaign is isolated."""
        super().__init__()
        self.name, self.sims, self.canvas, self.spinning = name, sims, canvas, spinning
        self.color = QColor(color)
        self.radius = 0.0
        self.turn = 0.0  # how far the dots have travelled round
        self._grab: QPointF | None = None
        self.setZValue(-1)  # under links and nodes
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(f"{name}" + ("\nIsolated: its ghosts use only its own runs" if spinning else "")
                        + "\nDrag the ring to move its runs together")
        self.fit()

    def fit(self) -> None:
        """Centre on the runs and reach just past the farthest one (and its label)."""
        if not self.sims:
            return
        xs = [s.x() for s in self.sims]
        ys = [s.y() for s in self.sims]
        center = QPointF((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        reach = max(math.hypot(s.x() - center.x(), s.y() - center.y()) for s in self.sims)
        radius = reach + G["ring_margin"]
        if radius != self.radius:
            self.prepareGeometryChange()
            self.radius = radius
        self.setPos(center)

    def spin(self, step: float) -> None:
        self.turn = (self.turn + step) % 1000.0
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        r = self.radius + G["ring_grab"]
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self) -> QPainterPath:  # the band along the ring, not the disc inside it
        circle = QPainterPath()
        circle.addEllipse(QPointF(0, 0), self.radius, self.radius)
        stroker = QPainterPathStroker()
        stroker.setWidth(2 * G["ring_grab"])
        return stroker.createStroke(circle)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        pen = QPen(self.color, G["ring_width"])
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([1.0, G["ring_gap"]])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashOffset(-self.turn)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._grab = event.scenePos()
        for sim in self.sims:
            sim.held = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._grab is None:
            return
        delta = event.scenePos() - self._grab
        self._grab = event.scenePos()
        for sim in self.sims:
            sim.setPos(sim.pos() + delta)
        self.fit()
        self.canvas.reheat(0.3)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._grab = None
        for sim in self.sims:
            sim.held = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.canvas.reheat(0.3)


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
        # No spatial index: during a settle every node and link moves each tick, and re-sorting an
        # index of thousands of items every time costs far more than it saves on clicks.
        self.graph_scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.graph_scene.setBackgroundBrush(QBrush(QColor(G["canvas"])))
        self.graph_scene.selectionChanged.connect(self._on_selection)
        self.setScene(self.graph_scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.rings: dict[str, CampaignRing] = {}
        self.spinner = QTimer(self)  # turns the campaign rings
        self.spinner.setInterval(G["ring_ms"])
        self.spinner.timeout.connect(self._spin)
        self.search = ""
        self.alpha = 0.0
        self.ticking = False  # inside _tick: nodes leave their links for the tick to move
        self._fit_pending = True
        self.timer = QTimer(self)
        self.timer.setInterval(layout.TICK_MS)
        self.timer.timeout.connect(self._tick)

        self.hint = QLabel(self, objectName="GraphHint", alignment=Qt.AlignmentFlag.AlignCenter)
        self.hint.hide()
        self.sidebar = None

    def set_sidebar(self, sidebar: QWidget) -> None:
        """Place the descriptor controls above the left edge of the graph canvas."""
        self.sidebar = sidebar
        sidebar.setParent(self)
        sidebar.attach_canvas(self)
        self._place_sidebar()
        sidebar.show()
        sidebar.raise_()

    def _place_sidebar(self) -> None:
        if self.sidebar is not None:
            self.sidebar.setGeometry(0, 0, min(330, self.viewport().width()), self.viewport().height())

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

    def set_campaigns(self, campaigns: list[dict[str, Any]]) -> None:
        """A ring for each campaign with runs on screen, round its runs and their ghosts:
        [{"name", "color", "sims": [bundle ids], "ghosts": [ghost node ids], "spinning": isolated}]."""
        for ring in self.rings.values():
            self.graph_scene.removeItem(ring)
        self.rings = {}
        for campaign in campaigns:
            sims = [self.nodes[s] for s in campaign["sims"] if isinstance(self.nodes.get(s), SimNode)]
            ghosts = [self.nodes[g] for g in campaign.get("ghosts", []) if isinstance(self.nodes.get(g), PredictionNode)]
            if sims:
                ring = CampaignRing(campaign["name"], campaign["color"], sims + ghosts, self, campaign.get("spinning", False))
                self.graph_scene.addItem(ring)
                self.rings[campaign["name"]] = ring
        if any(r.spinning for r in self.rings.values()):
            if not self.spinner.isActive():
                self.spinner.start()
        else:
            self.spinner.stop()

    def _spin(self) -> None:
        for ring in self.rings.values():
            if ring.spinning:
                ring.spin(G["ring_speed"])

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
        self._place_sidebar()
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
        if n == 0 or self.alpha < layout.ALPHA_MIN:
            self.timer.stop()
            return
        index = {node.key: i for i, node in enumerate(nodes)}
        x = np.array([node.x() for node in nodes])
        y = np.array([node.y() for node in nodes])
        vx = np.array([node.vx for node in nodes])
        vy = np.array([node.vy for node in nodes])
        held = np.array([node.held for node in nodes])
        edges = np.array([(index[e.a.key], index[e.b.key]) for e in self.edges], dtype=int).reshape(-1, 2)
        groups = [np.array([index[m.key] for m in ring.sims if m.key in index], dtype=int) for ring in self.rings.values()]
        x, y, vx, vy = layout.step(x, y, vx, vy, held, edges, groups, self.alpha)
        self.ticking = True
        try:
            for i, node in enumerate(nodes):
                node.vx, node.vy = float(vx[i]), float(vy[i])
                if not node.held:
                    node.setPos(float(x[i]), float(y[i]))
        finally:
            self.ticking = False
        for edge, (i, j) in zip(self.edges, edges.tolist()):
            edge.setLine(float(x[i]), float(y[i]), float(x[j]), float(y[j]))
        for ring in self.rings.values():
            ring.fit()

        self.alpha *= layout.COOLING
        if self._fit_pending and self.alpha < 0.25:
            self._fit_pending = False
            self.fit()

    # ---- view

    def fit(self) -> None:
        if not self.nodes:
            return
        rect = self.graph_scene.itemsBoundingRect().adjusted(-60, -60, 60, 60)  # rings included
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
            if self.sidebar is not None:
                self.sidebar.schedule_glass()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.itemAt(event.position().toPoint()) is None:
            self.graph_scene.clearSelection()
        super().mousePressEvent(event)
