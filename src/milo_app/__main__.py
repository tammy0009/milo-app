"""Start MILO: `uv run milo-app`, or the desktop icon (pythonw -m milo_app, no console)."""
from __future__ import annotations

import logging
import os
import sys

from milo_app.config import DATA


def main() -> None:
    if os.name == "nt":
        # Match the AppUserModelID on the shortcuts created by install-shortcut.ps1.
        # Set it before Qt creates any windows so the taskbar identifies MILO.
        import ctypes

        ctypes.OleDLL("shell32").SetCurrentProcessExplicitAppUserModelID("Milo.DesktopApp")

    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA / "milo-app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from PySide6.QtWidgets import QApplication

    from milo_app.ui import theme
    from milo_app.ui.window import MainWindow, delayed_start

    app = QApplication(sys.argv)
    app.setApplicationName("MILO")
    theme.apply(app)
    window = MainWindow()
    window.show()
    delayed_start(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
