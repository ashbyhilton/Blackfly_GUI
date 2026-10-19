"""Entry point: ``blackfly-gui [--simulate]`` or ``python -m blackfly_gui``."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blackfly-gui", description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="connect to a simulated camera (no hardware needed)")
    parser.add_argument("--serial", help="connect to the camera with this serial number on start")
    args = parser.parse_args(argv)

    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Blackfly GUI")
    app.setOrganizationName("BlackflyGUI")

    from .ui.main_window import MainWindow

    window = MainWindow(simulate=args.simulate, serial=args.serial)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
