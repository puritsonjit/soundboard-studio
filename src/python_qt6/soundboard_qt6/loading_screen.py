from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from .branding import brand_icon, brand_mark_pixmap


class LoadingScreen(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowIcon(brand_icon())
        self.setFixedSize(460, 320)

        card = QWidget(self)
        card.setObjectName("LoadingCard")
        card.setGeometry(0, 0, self.width(), self.height())

        layout = QVBoxLayout(card)
        layout.setContentsMargins(42, 36, 42, 34)
        layout.setSpacing(14)

        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setPixmap(brand_mark_pixmap(110))

        title = QLabel("Soundboard Studio")
        title.setObjectName("LoadingTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setMinimumHeight(44)

        subtitle = QLabel("Getting audio ready")
        subtitle.setObjectName("LoadingSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_label = QLabel("Starting up")
        self.status_label.setObjectName("LoadingStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)

        layout.addWidget(self.logo_label)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)

        self.setStyleSheet(
            """
            QWidget#LoadingCard {
                background: #151D2B;
                border-radius: 28px;
                border: 1px solid #263755;
            }
            QLabel#LoadingTitle {
                color: #F4F7FB;
                font-size: 28px;
                font-weight: 800;
                padding: 1px 0 3px 0;
            }
            QLabel#LoadingSubtitle {
                color: #9FB4D0;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#LoadingStatus {
                color: #EAF2FB;
                font-size: 13px;
            }
            QProgressBar {
                background: #0D1320;
                border: 1px solid #293A58;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: #29B6F6;
                border-radius: 5px;
            }
            """
        )
        title.setMinimumHeight(max(44, title.fontMetrics().height() + 4))
        self._center_on_screen()

    def set_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.status_label.setText(message)
        QApplication.processEvents()

    def set_brand_resources(self, resources_dir: Path) -> None:
        self.setWindowIcon(brand_icon(resources_dir))
        self.logo_label.setPixmap(brand_mark_pixmap(110, resources_dir))

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(geometry.center() - self.rect().center())
