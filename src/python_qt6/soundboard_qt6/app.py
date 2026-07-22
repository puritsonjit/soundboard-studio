from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .branding import brand_icon
from .loading_screen import LoadingScreen
from .main_window import MainWindow
from .paths import AppPaths
from .single_instance import SingleInstanceGuard


def configure_display_locale() -> None:
    """Keep user-facing numbers in the portable 0-9 format."""
    QLocale.setDefault(QLocale.c())


def main() -> int:
    configure_display_locale()
    app = QApplication(sys.argv)
    app.setApplicationName("Soundboard Studio")
    app.setOrganizationName("Soundboard Studio")
    app.setWindowIcon(brand_icon())
    identity_path = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
    instance_guard = SingleInstanceGuard(identity_path)
    if not instance_guard.acquired:
        QMessageBox.information(
            None,
            "Soundboard Studio",
            "Soundboard Studio is already running.",
        )
        instance_guard.close()
        return 0

    splash = LoadingScreen()
    splash.show()
    splash.set_progress(4, "Preparing folders")

    paths = AppPaths.build()
    app.setWindowIcon(brand_icon(paths.resources_dir))
    splash.set_brand_resources(paths.resources_dir)
    splash.set_progress(8, "Building interface")
    window = MainWindow(paths)

    def finish_startup() -> None:
        window.run_startup_sequence(splash.set_progress)
        splash.close()
        window.show()
        window.raise_()
        window.activateWindow()

    QTimer.singleShot(60, finish_startup)
    try:
        return app.exec()
    finally:
        window.shutdown()
        instance_guard.close()
