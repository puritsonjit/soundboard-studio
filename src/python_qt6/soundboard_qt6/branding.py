from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def asset_path(resources_dir: Path | None, name: str) -> Path | None:
    if resources_dir is None:
        resources_dir = default_resources_dir()
    return resources_dir / "assets" / name


def default_resources_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve() / "soundboard_qt6"
    return Path(__file__).resolve().parent


def brand_icon(resources_dir: Path | None = None) -> QIcon:
    icon_path = asset_path(resources_dir, "app_icon.png")
    if icon_path is not None and icon_path.is_file():
        return QIcon(str(icon_path))
    return QIcon(brand_mark_pixmap(256))


def brand_mark_pixmap(size: int, resources_dir: Path | None = None) -> QPixmap:
    icon_path = asset_path(resources_dir, "app_icon.png")
    if icon_path is not None and icon_path.is_file():
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            return pixmap.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = max(6, int(size * 0.075))
    radius = int(size * 0.24)
    rect = pixmap.rect().adjusted(margin, margin, -margin, -margin)

    shadow = QColor("#050A12")
    shadow.setAlpha(105)
    painter.setBrush(shadow)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect.translated(0, int(size * 0.035)), radius, radius)

    base_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    base_gradient.setColorAt(0.0, QColor("#35D7FF"))
    base_gradient.setColorAt(0.34, QColor("#172A42"))
    base_gradient.setColorAt(0.72, QColor("#0B1322"))
    base_gradient.setColorAt(1.0, QColor("#FF7446"))
    painter.setBrush(base_gradient)
    painter.setPen(QPen(QColor("#DDF8FF"), max(2, int(size * 0.018))))
    painter.drawRoundedRect(rect, radius, radius)

    clip_path = QPainterPath()
    clip_path.addRoundedRect(QRectF(rect), radius, radius)
    painter.setClipPath(clip_path)

    cyan = QColor("#3DD6FF")
    cyan.setAlpha(92)
    orange = QColor("#FF7043")
    orange.setAlpha(88)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(cyan)
    painter.drawEllipse(rect.left() - int(size * 0.22), rect.top() - int(size * 0.17), int(size * 0.52), int(size * 0.42))
    painter.setBrush(orange)
    painter.drawEllipse(rect.right() - int(size * 0.36), rect.bottom() - int(size * 0.32), int(size * 0.58), int(size * 0.46))
    painter.setClipping(False)

    pad_size = int(size * 0.215)
    gap = int(size * 0.055)
    grid_left = int(size * 0.268)
    grid_top = int(size * 0.225)
    pad_radius = int(size * 0.055)
    pad_colors = ("#FF7043", "#2DB7F0", "#67C36D", "#B45BD6")
    for index, color in enumerate(pad_colors):
        x = grid_left + (index % 2) * (pad_size + gap)
        y = grid_top + (index // 2) * (pad_size + gap)
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor("#EAF7FF"), max(2, int(size * 0.014))))
        painter.drawRoundedRect(x, y, pad_size, pad_size, pad_radius, pad_radius)

    wave_y = int(size * 0.765)
    wave_path = QPainterPath()
    wave_path.moveTo(int(size * 0.22), wave_y)
    wave_path.cubicTo(int(size * 0.31), int(size * 0.65), int(size * 0.40), int(size * 0.89), int(size * 0.50), wave_y)
    wave_path.cubicTo(int(size * 0.60), int(size * 0.64), int(size * 0.70), int(size * 0.87), int(size * 0.80), wave_y)

    painter.setPen(QPen(QColor("#29D7FF"), max(5, int(size * 0.052)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(wave_path)
    painter.setPen(QPen(QColor("#FFFFFF"), max(3, int(size * 0.034)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(wave_path)
    painter.end()
    return pixmap
