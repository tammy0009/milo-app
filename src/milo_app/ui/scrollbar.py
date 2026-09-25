"""A compact scrollbar that sits to the left of a tree instead of inside its right edge."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollBar, QTreeWidget


class LeftScrollBar(QScrollBar):
    def __init__(self, tree: QTreeWidget, page_step: int = 3) -> None:
        super().__init__(Qt.Orientation.Vertical, objectName="DescriptorScroll")
        self.setFixedWidth(14)
        self.setPageStep(page_step)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        native = tree.verticalScrollBar()
        native.rangeChanged.connect(self._sync_range)
        native.valueChanged.connect(self.setValue)
        self.valueChanged.connect(native.setValue)
        self._sync_range(native.minimum(), native.maximum())

    def _sync_range(self, minimum: int, maximum: int) -> None:
        self.setRange(minimum, maximum)
        self.setVisible(maximum > minimum)
