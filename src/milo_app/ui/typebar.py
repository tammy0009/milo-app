"""The row of switches above the graph: which kinds of node are shown."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from milo_app.ui import prefs

KINDS = (("sim", "Simulations"), ("descriptor", "Descriptors"), ("relationship", "Relationships"),
         ("prediction", "Predictions"))


class TypeBar(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TypeBar")
        self.store = prefs.store()
        hidden = self.store.value("hidden_kinds", [])
        hidden = set(hidden if isinstance(hidden, list) else [hidden] if hidden else [])
        self.chips: dict[str, QPushButton] = {}
        self.names = dict(KINDS)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(8)
        row.addWidget(QLabel("SHOW", objectName="PanelTitle"))
        for kind, name in KINDS:
            chip = QPushButton(name, objectName="Chip", checkable=True)
            chip.setChecked(kind not in hidden)
            chip.toggled.connect(self._toggled)
            row.addWidget(chip)
            self.chips[kind] = chip
        row.addStretch()

    @property
    def shown(self) -> set[str]:
        return {kind for kind, chip in self.chips.items() if chip.isChecked()}

    def set_counts(self, counts: dict[str, int]) -> None:
        for kind, chip in self.chips.items():
            chip.setText(f"{self.names[kind]}  {counts.get(kind, 0)}")

    def _toggled(self) -> None:
        self.store.setValue("hidden_kinds", [k for k, chip in self.chips.items() if not chip.isChecked()])
        self.changed.emit()
