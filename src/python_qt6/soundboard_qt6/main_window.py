from __future__ import annotations

import ctypes
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Iterable
import urllib.request
import uuid
import zipfile

import qrcode
from PySide6.QtCore import Qt, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from .asset_store import RuntimeAssetStore
from .audio_engine import QtAudioEngine
from .branding import asset_path, brand_icon, brand_mark_pixmap
from .clip_editor import SoundClipEditorDialog
from .config_manager import ConfigManager
from .hotkey_manager import (
    GlobalHotkeyManager,
    HotkeyError,
    normalize_hotkey,
    parse_hotkey,
)
from .internet_tunnel import InternetTunnel
from .models import AppConfig, LEGACY_PAD_COUNT, PAD_COUNT, PadConfig, default_pads
from .paths import AppPaths
from .remote_server import BrowserRemoteServer
from .shortcut_capture import ShortcutCaptureEdit
from .sound_validation import (
    RejectedSoundPackFile,
    SoundValidationError,
    scan_sound_pack,
    sound_pack_files,
    validate_sound_duration,
    validate_sound_pack,
)
from .windows_security import is_authenticode_signature_valid


SIGNED_CABLE_DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
SIGNED_CABLE_ZIP_NAME = "VBCABLE_Driver_Pack45.zip"
SIGNED_CABLE_EXTRACT_DIR = "VBCABLE_Driver_Pack45"
SIGNED_CABLE_SETUP_EXE = "VBCABLE_Setup_x64.exe"
SIGNED_CABLE_INF = "vbMmeCable64_win10.inf"
SIGNED_CABLE_HARDWARE_ID = "VBAudioVACWDM"
SIGNED_CABLE_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
SIGNED_CABLE_MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
SIGNED_CABLE_RECHECK_DELAY_MS = 30_000
PROFILE_MANIFEST_NAME = "soundboard_profile.json"
PROFILE_VERSION = 4
PROFILE_MAX_MANIFEST_BYTES = 1024 * 1024
PROFILE_MAX_ASSET_BYTES = 50 * 1024 * 1024
PROFILE_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
PROFILE_MAX_ENTRIES = 2048
QR_DISPLAY_SIZE = 96
PAD_GRID_COLUMNS = 6
PAD_GRID_ROWS = math.ceil(PAD_COUNT / PAD_GRID_COLUMNS)


class PadButton(QPushButton):
    clicked_for_edit = Signal(str)

    def __init__(self, pad_id: str) -> None:
        super().__init__()
        self.pad_id = pad_id
        self._label = pad_id
        self._color_hex = "#3A4A63"
        self._image_path = ""
        self._pixmap = QPixmap()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.clicked.connect(lambda: self.clicked_for_edit.emit(self.pad_id))

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(48, 72)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(120, 120)

    def set_pad_display(self, pad: PadConfig) -> None:
        self._label = pad.label
        self._color_hex = pad.color_hex
        self._image_path = pad.image_path
        self._pixmap = QPixmap(pad.image_path) if pad.image_path and Path(pad.image_path).is_file() else QPixmap()
        self.setText(pad.label)
        self.setToolTip(pad.label)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        painter.setClipPath(path)

        color = QColor(self._color_hex if QColor(self._color_hex).isValid() else "#3A4A63")
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, color.lighter(108))
        gradient.setColorAt(1.0, color.darker(118))
        painter.fillPath(path, gradient)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = rect.x() + (rect.width() - scaled.width()) / 2
            y = rect.y() + (rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
            painter.fillPath(path, QColor(0, 0, 0, 86))

        if self.isDown():
            painter.fillPath(path, QColor(0, 0, 0, 38))

        painter.setClipping(False)
        border_color = QColor("#FFFFFF") if self.isChecked() else QColor(255, 255, 255, 0)
        painter.setPen(QPen(border_color, 4))
        painter.drawRoundedRect(rect, 18, 18)

        text_rect = rect.adjusted(8, 8, -8, -8)
        font = painter.font()
        font.setBold(True)
        available_width = max(1, int(text_rect.width()))
        for point_size in range(14, 8, -1):
            font.setPointSize(point_size)
            painter.setFont(font)
            if painter.fontMetrics().horizontalAdvance(self._label) <= available_width:
                break
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        display_text = painter.fontMetrics().elidedText(
            self._label,
            Qt.TextElideMode.ElideRight,
            available_width,
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, display_text)
        painter.end()


class SoundValidationDialog(QDialog):
    def __init__(self, parent: QWidget, message: str, title: str = "Sound not added") -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self.setFixedWidth(520)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("SoundWarningCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 18)
        card_layout.setSpacing(18)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        warning_mark = QLabel("!")
        warning_mark.setObjectName("SoundWarningMark")
        warning_mark.setFixedSize(44, 44)
        warning_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)

        copy_layout = QVBoxLayout()
        copy_layout.setContentsMargins(0, 1, 0, 0)
        copy_layout.setSpacing(7)

        heading = QLabel(title)
        heading.setObjectName("SoundWarningHeading")
        body = QLabel(message)
        body.setObjectName("SoundWarningBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        copy_layout.addWidget(heading)
        copy_layout.addWidget(body)

        content_layout.addWidget(warning_mark)
        content_layout.addLayout(copy_layout, 1)
        card_layout.addLayout(content_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("OK")
        ok_button.setObjectName("SoundWarningOk")
        ok_button.setFixedSize(92, 38)
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        button_layout.addWidget(ok_button)
        card_layout.addLayout(button_layout)

        outer_layout.addWidget(card)
        self.setStyleSheet(
            """
            QFrame#SoundWarningCard {
                background: #182132;
                border: 1px solid #354561;
                border-radius: 18px;
            }
            QLabel#SoundWarningMark {
                color: #10131A;
                background: #FFB84D;
                border: 1px solid #FFD58A;
                border-radius: 14px;
                font-size: 26px;
                font-weight: 900;
            }
            QLabel#SoundWarningHeading {
                color: #F7FAFF;
                background: transparent;
                border: none;
                font-size: 17px;
                font-weight: 700;
                padding: 1px 0 2px 0;
            }
            QLabel#SoundWarningBody {
                color: #BFCBDD;
                background: transparent;
                border: none;
                font-size: 13px;
            }
            QPushButton#SoundWarningOk {
                color: #07101C;
                background: #6FF5A8;
                border: 1px solid #A8FFCB;
                border-radius: 12px;
                padding: 0;
                font-weight: 700;
            }
            QPushButton#SoundWarningOk:hover {
                background: #8BFFBC;
            }
            QPushButton#SoundWarningOk:pressed {
                background: #54D98C;
            }
            QPushButton#SoundWarningOk:focus {
                border: 2px solid #FFFFFF;
            }
            """
        )


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.config_manager = ConfigManager(paths.config_path, paths.sounds_dir)
        self.config: AppConfig = self.config_manager.load()
        self.asset_store = RuntimeAssetStore(paths.sounds_dir, paths.images_dir)
        self.audio_engine = QtAudioEngine()
        self.internet_tunnel = InternetTunnel(paths.runtime_dir / "tools")
        self.remote_server = BrowserRemoteServer(
            web_dir=self.paths.web_dir,
            port=self.config.server_port,
            state_provider=self.remote_state,
            image_resolver=self.resolve_pad_image,
        )
        application = QApplication.instance()
        if application is None:
            raise RuntimeError("Soundboard Studio requires an active Qt application.")
        self.hotkey_manager = GlobalHotkeyManager(application)
        self._remote_access_token = ""
        self._shutdown_complete = False
        self.selected_pad_id = self.config.pads[0].id
        self.pad_buttons: dict[str, PadButton] = {}
        self._auto_cable_install_started = False
        self._editor_sound_mode = "single"
        self._editor_single_sound_path = ""
        self._editor_pack_path = ""
        self._preferred_input_missing = False
        self._preferred_output_missing = False

        self.setWindowTitle("Soundboard Studio")
        self.setWindowIcon(brand_icon(self.paths.resources_dir))
        self.resize(1360, 860)
        self.setMinimumSize(1024, 680)
        self._build_ui()
        self._apply_styles()
        self._connect_signals()
        self.audio_engine.set_monitor_muted(self.config.monitor_muted)
        self.audio_engine.set_playback_volume_percent(self.config.playback_volume_percent)
        self.audio_engine.set_pad_volumes(self.config.pads)
        self.audio_engine.set_mic_noise_gate(
            self.config.mic_noise_gate_enabled,
            self.config.mic_noise_gate_threshold_db,
        )
        self.audio_engine.set_raw_hd_soundboard(self.config.raw_hd_soundboard_enabled)
        self._set_startup_progress(5, "Preparing studio")
        if self.config_manager.last_warning:
            self.log(self.config_manager.last_warning)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Native font metrics settle only after the window is polished for its
        # screen. Re-evaluate here so Windows display scaling cannot crop titles.
        self._update_heading_minimum_heights()

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.internet_tunnel.stop()
        self.remote_server.stop()
        self.hotkey_manager.close()
        self.audio_engine.shutdown()

    def run_startup_sequence(self, progress_callback: Callable[[int, str], None] | None = None) -> None:
        steps = [
            (15, "Checking audio devices", self.refresh_devices),
            (35, "Loading pad settings", self.refresh_editor),
            (55, "Building pad board", self.refresh_pad_buttons),
            (64, "Securing pad files", self.ensure_runtime_assets),
            (72, "Warming up sounds", lambda: self.audio_engine.preload_pads(self.config.pads)),
            (78, "Activating shortcuts", self.refresh_hotkeys),
            (86, "Starting phone remote", self._start_remote_for_startup),
            (94, "Checking virtual mic route", self.ensure_virtual_mic),
        ]

        for value, message, action in steps:
            self._set_startup_progress(value, message)
            if progress_callback is not None:
                progress_callback(value, message)
            try:
                action()
            except Exception as exc:
                self.log(f"Startup skipped one step ({message}): {exc}")
            QApplication.processEvents()

        self._set_startup_progress(100, "Ready")
        if progress_callback is not None:
            progress_callback(100, "Ready")
        self.startup_status_label.hide()
        self.startup_progress.hide()

    def _start_remote_for_startup(self) -> None:
        self._prepare_remote_access()
        self.remote_server.start()

    def _set_startup_progress(self, value: int, message: str) -> None:
        if hasattr(self, "startup_progress"):
            self.startup_progress.setValue(value)
        if hasattr(self, "startup_status_label"):
            self.startup_status_label.setText(f"Status: {message}")
        QApplication.processEvents()

    @staticmethod
    def _make_label_responsive(label: QLabel) -> None:
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    @staticmethod
    def _make_combo_responsive(combo: QComboBox) -> None:
        view = QListView(combo)
        view.setObjectName("ComboPopup")
        view.setUniformItemSizes(True)
        view.setSpacing(2)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        view.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        combo.setView(view)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.setFixedHeight(32)

    def _device_status_text(self, input_count: int, output_count: int) -> str:
        return f"Found {input_count} mic(s) and {output_count} playback device(s)"

    @staticmethod
    def _compact_mixer_status(status: str) -> str:
        lowered = status.lower()
        if "mixer running" in lowered and "virtual mic active" in lowered:
            return "Ready: mic + pads are available on CABLE Output"
        if "mixer idle" in lowered:
            return "Choose your microphone and playback device"
        if "mixer stopped" in lowered:
            return "Audio engine stopped"
        if len(status) > 120:
            return status[:117].rstrip() + "..."
        return f"Audio: {status}"

    @staticmethod
    def _compact_driver_status(message: str) -> str:
        lowered = message.lower()
        if "cable output is available" in lowered:
            return "Voice app input: CABLE Output is ready"
        if "installed" in lowered:
            return "Virtual mic route installed"
        if "missing" in lowered:
            return "Virtual mic package is missing"
        if "incomplete" in lowered:
            return "Virtual mic package is incomplete"
        if "test signing" in lowered:
            return "Built-in driver needs signing"
        return message if len(message) <= 52 else message[:49].rstrip() + "..."

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(7)

        top_card = QFrame()
        top_card.setMaximumHeight(140)
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(14, 14, 14, 14)
        top_layout.setSpacing(14)

        brand_label = QLabel()
        brand_label.setObjectName("BrandMark")
        brand_label.setFixedSize(54, 54)
        brand_label.setPixmap(brand_mark_pixmap(54, self.paths.resources_dir))
        brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(brand_label, 0, Qt.AlignmentFlag.AlignTop)

        title_column = QVBoxLayout()
        self.title_label = QLabel("Soundboard Studio")
        self.title_label.setObjectName("TitleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setMinimumHeight(36)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.server_status_label = QLabel("Remote: starting")
        self.virtual_mic_status_label = QLabel(
            "Voice route: checking"
        )
        self.profile_status_label = QLabel(f"{len(self.config.pads)} pads loaded")
        self.startup_status_label = QLabel("Status: preparing")
        self.startup_progress = QProgressBar()
        self.startup_progress.setRange(0, 100)
        self.startup_progress.setValue(0)
        self.startup_progress.setTextVisible(False)
        self.startup_progress.setFixedHeight(9)
        for label in (
            self.server_status_label,
            self.virtual_mic_status_label,
            self.profile_status_label,
            self.startup_status_label,
        ):
            self._make_label_responsive(label)
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.server_status_label)
        title_column.addWidget(self.virtual_mic_status_label)
        title_column.addWidget(self.profile_status_label)
        title_column.addWidget(self.startup_status_label)
        title_column.addWidget(self.startup_progress)
        top_layout.addLayout(title_column, 1)

        remote_column = QHBoxLayout()
        remote_column.setSpacing(12)
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(QR_DISPLAY_SIZE, QR_DISPLAY_SIZE)
        self.remote_url_label = QLabel()
        self.remote_url_label.setWordWrap(True)
        self.remote_client_label = QLabel("Phones connected: 0")
        remote_text = QVBoxLayout()
        remote_text.setSpacing(4)
        remote_title = QLabel("Phone Remote")
        remote_title.setObjectName("SectionTitle")
        self.remote_mode_combo = QComboBox()
        self.remote_mode_combo.addItem("Local", "local")
        self.remote_mode_combo.addItem("Internet", "internet")
        self.remote_mode_combo.setToolTip(
            "Local is fastest on the same Wi-Fi. Internet creates a temporary private HTTPS link."
        )
        self._make_combo_responsive(self.remote_mode_combo)
        self.remote_mode_combo.setFixedWidth(126)
        self.remote_mode_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        mode_index = self.remote_mode_combo.findData(self.config.remote_connection_mode)
        self.remote_mode_combo.setCurrentIndex(max(0, mode_index))
        remote_title_row = QHBoxLayout()
        remote_title_row.setSpacing(8)
        remote_title_row.addWidget(remote_title)
        remote_title_row.addWidget(self.remote_mode_combo)
        remote_title_row.addStretch(1)
        self._make_label_responsive(remote_title)
        remote_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._make_label_responsive(self.remote_url_label)
        self._make_label_responsive(self.remote_client_label)
        remote_text.addLayout(remote_title_row)
        remote_text.addWidget(self.remote_url_label)
        remote_text.addWidget(self.remote_client_label)
        remote_column.addWidget(self.qr_label)
        remote_column.addLayout(remote_text)
        top_layout.addLayout(remote_column, 1)

        button_column = QVBoxLayout()
        self.start_server_button = QPushButton("Restart")
        self.stop_server_button = QPushButton("Stop")
        button_column.addWidget(self.start_server_button)
        button_column.addWidget(self.stop_server_button)
        button_column.addStretch(1)
        top_layout.addLayout(button_column)

        root_layout.addWidget(top_card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(2)

        left_panel = QFrame()
        left_panel.setMinimumWidth(280)
        left_panel.setMaximumWidth(460)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(5)

        self.page_urls_label = QLabel()
        self.page_urls_label.hide()

        devices_title = QLabel("Voice & Playback")
        devices_title.setObjectName("SectionTitle")
        self.device_status_label = QLabel()
        self.driver_status_label = QLabel()
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self._make_label_responsive(self.device_status_label)
        self._make_label_responsive(self.driver_status_label)
        self._make_combo_responsive(self.input_combo)
        self._make_combo_responsive(self.output_combo)
        self.mic_gate_checkbox = QCheckBox("Mic noise gate")
        self.mic_gate_checkbox.setToolTip("Helps reduce keyboard and mouse noise from your real microphone.")
        self.mic_gate_checkbox.setChecked(self.config.mic_noise_gate_enabled)
        self.mic_gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gate_slider.setRange(-70, -20)
        self.mic_gate_slider.setValue(int(round(self.config.mic_noise_gate_threshold_db)))
        self.mic_gate_slider.setEnabled(self.config.mic_noise_gate_enabled)
        self.mic_gate_value_label = QLabel(self._mic_gate_label_text())
        self.mic_gate_value_label.setToolTip("Higher is stricter. Lower lets quieter speech through.")
        self.discord_tip_label = QLabel(
            "Discord: CABLE Output | Studio mode"
        )
        self.discord_tip_label.setToolTip(
            "Select CABLE Output in Discord or TeamSpeak. Discord Studio mode preserves soundboard quality best."
        )
        self.discord_tip_label.setWordWrap(True)
        self._make_label_responsive(self.mic_gate_value_label)
        self._make_label_responsive(self.discord_tip_label)
        self.mute_monitor_checkbox = QCheckBox("Mute local playback")
        self.mute_monitor_checkbox.setToolTip("Mutes only what you hear. The virtual mic route stays active.")
        self.mute_monitor_checkbox.setChecked(self.config.monitor_muted)
        self.playback_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_volume_slider.setRange(0, 200)
        self.playback_volume_slider.setValue(max(0, min(200, int(self.config.playback_volume_percent))))
        self.playback_volume_value_label = QLabel(self._playback_volume_label_text())
        self.playback_volume_value_label.setToolTip("Only changes what you hear locally. Voice chat output stays unchanged.")
        playback_volume_row = QHBoxLayout()
        playback_volume_row.setSpacing(8)
        playback_volume_row.addWidget(QLabel("Local playback volume"))
        playback_volume_row.addStretch(1)
        playback_volume_row.addWidget(self.playback_volume_value_label)
        self.raw_hd_checkbox = QCheckBox("HD soundboard mode")
        self.raw_hd_checkbox.setToolTip(
            "Experimental: sends less-processed pad audio into the virtual mic. "
            "Turn off for the safer Discord processing path."
        )
        self.raw_hd_checkbox.setChecked(self.config.raw_hd_soundboard_enabled)
        refresh_devices_button = QPushButton("Refresh devices")
        install_cable_button = QPushButton("Install virtual mic")
        left_layout.addWidget(devices_title)
        left_layout.addWidget(self.device_status_label)
        self.device_status_label.hide()
        left_layout.addWidget(self.driver_status_label)
        left_layout.addWidget(QLabel("Microphone"))
        left_layout.addWidget(self.input_combo)
        left_layout.addWidget(self.mic_gate_checkbox)
        left_layout.addWidget(self.mic_gate_value_label)
        left_layout.addWidget(self.mic_gate_slider)
        left_layout.addWidget(self.discord_tip_label)
        left_layout.addWidget(QLabel("Headphones / speakers"))
        left_layout.addWidget(self.output_combo)
        left_layout.addWidget(self.mute_monitor_checkbox)
        left_layout.addLayout(playback_volume_row)
        left_layout.addWidget(self.playback_volume_slider)
        left_layout.addWidget(self.raw_hd_checkbox)
        device_button_row = QHBoxLayout()
        device_button_row.setSpacing(8)
        device_button_row.addWidget(refresh_devices_button)
        device_button_row.addWidget(install_cable_button)
        left_layout.addLayout(device_button_row)
        left_layout.addStretch(1)
        self.refresh_devices_button = refresh_devices_button
        self.install_cable_button = install_cable_button

        center_panel = QFrame()
        center_panel.setMinimumWidth(420)
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(12, 12, 12, 12)
        center_layout.setSpacing(8)
        pad_board_title = QLabel("Sound Pads")
        pad_board_title.setObjectName("SectionTitle")
        center_layout.addWidget(pad_board_title)
        self.pad_grid = QGridLayout()
        self.pad_grid.setHorizontalSpacing(8)
        self.pad_grid.setVerticalSpacing(8)
        for column in range(PAD_GRID_COLUMNS):
            self.pad_grid.setColumnStretch(column, 1)
        for row in range(PAD_GRID_ROWS):
            self.pad_grid.setRowStretch(row, 1)
        grid_host = QWidget()
        grid_host.setLayout(self.pad_grid)
        center_layout.addWidget(grid_host, 1)

        right_panel = QFrame()
        right_panel.setMinimumWidth(280)
        right_panel.setMaximumWidth(380)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(4)
        editor_title = QLabel("Pad Setup")
        editor_title.setObjectName("SectionTitle")
        right_layout.addWidget(editor_title)
        self.editor_status_label = QLabel("Select a pad to edit.")
        right_layout.addWidget(self.editor_status_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        self.pad_selector = QComboBox()
        self.sound_mode_combo = QComboBox()
        self.sound_mode_combo.addItem("Single", "single")
        self.sound_mode_combo.addItem("Sound pack", "pack")
        self.sound_mode_combo.setToolTip(
            "Use one clip, or choose a folder and play a random clip on every trigger."
        )
        self.label_edit = QLineEdit()
        self.label_edit.setMaxLength(48)
        self.color_edit = QLineEdit()
        self.sound_edit = QLineEdit()
        self.image_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Button name")
        self.color_edit.setPlaceholderText("#FF7043")
        self.sound_edit.setPlaceholderText("Choose or edit an audio clip")
        self.image_edit.setPlaceholderText("Optional button image")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setTickInterval(25)
        self.volume_slider.setSingleStep(5)
        self.volume_value_label = QLabel("100%")
        self.hotkey_edit = ShortcutCaptureEdit()
        self.hotkey_edit.setMaximumSequenceLength(1)
        self.hotkey_edit.setClearButtonEnabled(True)
        self.hotkey_edit.setToolTip(
            "Optional global shortcut. Press one key combination; it is applied automatically."
        )
        hotkey_line_edit = self.hotkey_edit.findChild(QLineEdit)
        if hotkey_line_edit is not None:
            hotkey_line_edit.setPlaceholderText("Not set")
        self._make_combo_responsive(self.pad_selector)
        self._make_combo_responsive(self.sound_mode_combo)
        self.sound_mode_combo.setFixedWidth(112)
        self.sound_mode_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        for line_edit in (self.label_edit, self.color_edit, self.sound_edit, self.image_edit):
            line_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.sound_edit.setMinimumWidth(0)
        self.image_edit.setMinimumWidth(72)
        form.addRow("Pad", self.pad_selector)
        form.addRow("Name", self.label_edit)
        form.addRow("Color", self.color_edit)

        volume_row = QWidget()
        volume_row_layout = QHBoxLayout(volume_row)
        volume_row_layout.setContentsMargins(0, 0, 0, 0)
        volume_row_layout.setSpacing(8)
        self.volume_value_label.setMinimumWidth(44)
        volume_row_layout.addWidget(self.volume_slider, 1)
        volume_row_layout.addWidget(self.volume_value_label)
        form.addRow("Volume", volume_row)

        hotkey_row = QWidget()
        hotkey_row_layout = QHBoxLayout(hotkey_row)
        hotkey_row_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_row_layout.setSpacing(8)
        self.clear_hotkey_button = QPushButton("Clear")
        self.clear_hotkey_button.setObjectName("CompactButton")
        self.clear_hotkey_button.setFixedSize(64, 30)
        self.clear_hotkey_button.setToolTip("Remove this pad's shortcut immediately.")
        hotkey_row_layout.addWidget(self.hotkey_edit, 1)
        hotkey_row_layout.addWidget(self.clear_hotkey_button)
        form.addRow("Shortcut", hotkey_row)

        sound_row = QWidget()
        sound_row_layout = QVBoxLayout(sound_row)
        sound_row_layout.setContentsMargins(0, 0, 0, 0)
        sound_row_layout.setSpacing(3)
        self.sound_browse_button = QPushButton("Add")
        self.sound_edit_button = QPushButton("Edit")
        self.sound_browse_button.setObjectName("CompactButton")
        self.sound_edit_button.setObjectName("CompactButton")
        self.sound_browse_button.setFixedWidth(64)
        self.sound_edit_button.setFixedWidth(64)
        self.sound_browse_button.setFixedHeight(30)
        self.sound_edit_button.setFixedHeight(30)
        self.sound_browse_button.setToolTip("Choose an audio file and preview it before adding it.")
        self.sound_edit_button.setToolTip("Trim, replace, or preview this pad's audio clip.")
        self.sound_summary_label = QLabel()
        self.sound_summary_label.setObjectName("MutedLabel")
        self.sound_summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sound_action_row = QHBoxLayout()
        sound_action_row.setSpacing(8)
        sound_action_row.addWidget(self.sound_summary_label, 1)
        sound_action_row.addWidget(self.sound_browse_button)
        sound_action_row.addWidget(self.sound_edit_button)
        # Isolate the compact source line in its own widget so QFormLayout cannot
        # wrap its children independently and place them on top of one another.
        sound_source_widget = QWidget()
        sound_source_widget.setFixedHeight(32)
        sound_source_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        sound_source_row = QHBoxLayout(sound_source_widget)
        sound_source_row.setContentsMargins(0, 0, 0, 0)
        sound_source_row.setSpacing(6)
        sound_source_row.addWidget(self.sound_mode_combo)
        sound_source_row.addWidget(self.sound_edit, 1)
        sound_row_layout.addWidget(sound_source_widget)
        sound_row_layout.addLayout(sound_action_row)
        form.addRow("Sound", sound_row)

        image_row = QWidget()
        image_row_layout = QHBoxLayout(image_row)
        image_row_layout.setContentsMargins(0, 0, 0, 0)
        image_row_layout.setSpacing(8)
        self.image_browse_button = QPushButton("Browse")
        self.image_browse_button.setObjectName("CompactButton")
        self.image_browse_button.setFixedWidth(80)
        self.image_browse_button.setFixedHeight(30)
        image_row_layout.addWidget(self.image_edit, 1)
        image_row_layout.addWidget(self.image_browse_button)
        form.addRow("Image", image_row)
        right_layout.addLayout(form)

        action_row = QHBoxLayout()
        self.test_button = QPushButton("Preview locally")
        self.save_button = QPushButton("Save pad")
        for button in (self.test_button, self.save_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.test_button, 1)
        action_row.addWidget(self.save_button, 1)
        right_layout.addLayout(action_row)

        profile_row = QHBoxLayout()
        self.export_profile_button = QPushButton("Export ZIP")
        self.import_profile_button = QPushButton("Import ZIP")
        self.export_profile_button.setToolTip("Back up pads, volumes, sounds, and images into one ZIP file.")
        self.import_profile_button.setToolTip("Restore pads, volumes, sounds, and images from a ZIP backup.")
        for button in (self.export_profile_button, self.import_profile_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        profile_row.addWidget(self.export_profile_button, 1)
        profile_row.addWidget(self.import_profile_button, 1)
        right_layout.addLayout(profile_row)
        right_layout.addStretch(1)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 700, 320])

        root_layout.addWidget(splitter, 1)

        log_card = QFrame()
        log_card.setFixedHeight(80)
        log_layout = QHBoxLayout(log_card)
        log_layout.setContentsMargins(14, 12, 14, 12)
        log_layout.setSpacing(12)
        log_title = QLabel("Activity")
        log_title.setObjectName("SectionTitle")
        log_title.setMinimumWidth(72)
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("ActivityLog")
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_box.setMaximumBlockCount(400)
        self.log_box.setFixedHeight(56)
        self.log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_layout.addWidget(log_title, 0, Qt.AlignmentFlag.AlignVCenter)
        log_layout.addWidget(self.log_box, 1)
        self.activity_card = log_card
        root_layout.addWidget(log_card)

        self.setCentralWidget(root)

        test_action = QAction("Preview Selected Pad", self)
        test_action.triggered.connect(self.play_selected_pad)
        self.addAction(test_action)

    def _apply_styles(self) -> None:
        arrow = asset_path(self.paths.resources_dir, "combo_arrow.svg")
        arrow_css = f"image: url({arrow.as_posix()});" if arrow is not None and arrow.is_file() else "image: none;"
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: #10131A; color: #F4F7FB; }}
            QFrame {{
                background: #182132;
                border-radius: 18px;
            }}
            QSplitter::handle {{
                background: #10131A;
                margin: 0 3px;
            }}
            QSplitter::handle:horizontal {{
                width: 7px;
            }}
            QLabel {{ color: #EAF2FB; }}
            QLabel#BrandMark {{
                background: transparent;
                border: none;
            }}
            QCheckBox {{ color: #EAF2FB; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 1px solid #5C6C86;
                background: #0F1520;
            }}
            QCheckBox::indicator:checked {{
                background: #29B6F6;
                border: 1px solid #8CE3FF;
            }}
            QSlider::groove:horizontal {{
                height: 7px;
                border-radius: 4px;
                background: #0F1520;
            }}
            QSlider::sub-page:horizontal {{
                border-radius: 4px;
                background: #29B6F6;
            }}
            QSlider::handle:horizontal {{
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
                background: #EAF2FB;
            }}
            QProgressBar {{
                background: #0F1520;
                border: 1px solid #2D3B56;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: #29B6F6;
                border-radius: 4px;
            }}
            QLabel#TitleLabel {{
                font-size: 28px;
                font-weight: 700;
                padding: 1px 0 3px 0;
            }}
            QLabel#SectionTitle {{
                font-size: 18px;
                font-weight: 600;
                padding: 1px 0 2px 0;
            }}
            QPushButton {{
                background: #25324A;
                border: none;
                border-radius: 14px;
                padding: 10px 14px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #30405E; }}
            QPushButton#CompactButton {{
                border-radius: 10px;
                padding: 5px 10px;
            }}
            QLineEdit, QComboBox, QKeySequenceEdit, QPlainTextEdit {{
                background: #0F1520;
                border: 1px solid #2D3B56;
                border-radius: 10px;
                color: #F3F7FB;
                padding: 6px 8px;
            }}
            QKeySequenceEdit:focus {{
                border-color: #29B6F6;
            }}
            QPlainTextEdit#ActivityLog {{
                background: #111827;
                border-color: #31425F;
                border-radius: 12px;
                padding: 7px 10px;
            }}
            QComboBox {{
                padding: 6px 36px 6px 10px;
            }}
            QComboBox:hover {{
                border-color: #496286;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border: none;
                border-left: 1px solid #273653;
                border-top-right-radius: 10px;
                border-bottom-right-radius: 10px;
                background: #111A29;
            }}
            QComboBox::drop-down:hover {{
                background: #1B2840;
            }}
            QComboBox::down-arrow {{
                {arrow_css}
                width: 14px;
                height: 14px;
            }}
            QComboBox QAbstractItemView, QListView#ComboPopup {{
                background: #101827;
                border: 1px solid #344563;
                border-radius: 12px;
                color: #F3F7FB;
                padding: 5px;
                outline: 0;
                selection-background-color: #253A5E;
                selection-color: #FFFFFF;
            }}
            QListView#ComboPopup::item {{
                min-height: 28px;
                padding: 7px 10px;
                border-radius: 8px;
            }}
            QListView#ComboPopup::item:hover {{
                background: #20324E;
            }}
            QListView#ComboPopup::item:selected {{
                background: #2B8DD8;
                color: #FFFFFF;
            }}
            QListWidget {{
                background: #0F1520;
                border: 1px solid #2D3B56;
                border-radius: 12px;
                color: #F3F7FB;
            }}
            """
        )
        self._update_heading_minimum_heights()

    def _update_heading_minimum_heights(self) -> None:
        self.title_label.setMinimumHeight(max(36, self.title_label.fontMetrics().height() + 4))
        for heading in self.findChildren(QLabel, "SectionTitle"):
            heading.setMinimumHeight(heading.fontMetrics().height() + 2)

    def _connect_signals(self) -> None:
        self.start_server_button.clicked.connect(self.handle_restart_remote)
        self.stop_server_button.clicked.connect(self.handle_stop_remote)
        self.remote_mode_combo.currentIndexChanged.connect(self.handle_remote_connection_mode_changed)
        self.refresh_devices_button.clicked.connect(self.rescan_devices)
        self.install_cable_button.clicked.connect(self.handle_install_signed_cable)
        self.sound_browse_button.clicked.connect(self.browse_sound_source)
        self.sound_edit_button.clicked.connect(self.edit_sound_file)
        self.sound_mode_combo.currentIndexChanged.connect(self.handle_sound_mode_changed)
        self.image_browse_button.clicked.connect(self.browse_image_file)
        self.export_profile_button.clicked.connect(self.export_profile_zip)
        self.import_profile_button.clicked.connect(self.import_profile_zip)
        self.save_button.clicked.connect(self.save_selected_pad)
        self.test_button.clicked.connect(self.play_selected_pad)
        self.hotkey_edit.keySequenceChanged.connect(self.save_current_hotkey)
        self.hotkey_edit.editingFinished.connect(self.save_current_hotkey)
        self.clear_hotkey_button.clicked.connect(self.clear_current_hotkey)
        self.pad_selector.currentIndexChanged.connect(self._sync_selected_from_combo)
        self.volume_slider.valueChanged.connect(self.handle_pad_volume_changed)
        self.input_combo.currentIndexChanged.connect(self.save_device_selection)
        self.output_combo.currentIndexChanged.connect(self.save_device_selection)
        self.mic_gate_checkbox.toggled.connect(self.save_mic_noise_gate)
        self.mic_gate_slider.valueChanged.connect(self.save_mic_noise_gate)
        self.mute_monitor_checkbox.toggled.connect(self.save_monitor_mute)
        self.playback_volume_slider.valueChanged.connect(self.save_playback_volume)
        self.raw_hd_checkbox.toggled.connect(self.save_raw_hd_soundboard_mode)

        self.audio_engine.log.connect(self.log)
        self.audio_engine.status_changed.connect(self.update_mixer_status)
        self.audio_engine.playback_state_changed.connect(self.remote_server.broadcast_playback_state)
        self.audio_engine.readiness_changed.connect(self.remote_server.broadcast_audio_status)
        self.remote_server.log.connect(self.log)
        self.remote_server.status_changed.connect(self.update_remote_status)
        self.remote_server.trigger_requested.connect(self.handle_remote_trigger)
        self.remote_server.stop_requested.connect(self.handle_remote_stop_all)
        self.remote_server.playback_mode_changed.connect(self.handle_remote_playback_mode_changed)
        self.remote_server.client_count_changed.connect(self.update_client_count)
        self.remote_server.urls_changed.connect(self.update_remote_urls)
        self.internet_tunnel.log.connect(self.log)
        self.internet_tunnel.status_changed.connect(self.update_internet_tunnel_status)
        self.internet_tunnel.url_changed.connect(lambda _url: self.update_remote_card())
        self.hotkey_manager.triggered.connect(self.handle_hotkey_trigger)

    def refresh_devices(self) -> None:
        try:
            input_devices = self.audio_engine.available_input_devices()
            output_devices = self.audio_engine.available_output_devices()
        except Exception as exc:
            self.log(f"Could not refresh audio devices: {exc}")
            self.device_status_label.setText("Audio device refresh failed")
            self.device_status_label.setToolTip(str(exc))
            return

        self.input_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        self.input_combo.clear()
        self.output_combo.clear()

        for device_id, name in input_devices:
            self.input_combo.addItem(name, device_id)
        for device_id, name in output_devices:
            self.output_combo.addItem(name, device_id)

        input_selection_restored = self._select_combo_device(
            self.input_combo,
            self.config.selected_input_device_id,
            self.config.selected_input_device_name,
        )
        output_selection_restored = self._select_combo_device(
            self.output_combo,
            self.config.selected_output_device_id,
            self.config.selected_output_device_name,
        )
        self.input_combo.blockSignals(False)
        self.output_combo.blockSignals(False)
        missing_preferred_input = bool(
            self.config.selected_input_device_id or self.config.selected_input_device_name
        ) and not input_selection_restored
        missing_preferred_output = bool(
            self.config.selected_output_device_id or self.config.selected_output_device_name
        ) and not output_selection_restored
        self._preferred_input_missing = missing_preferred_input
        self._preferred_output_missing = missing_preferred_output
        if not missing_preferred_input and not missing_preferred_output:
            self.save_device_selection()
        else:
            # Use available fallbacks without forgetting a temporarily offline
            # USB or wireless device. A manual selection still saves normally.
            self.input_combo.setToolTip(self.input_combo.currentText())
            self.output_combo.setToolTip(self.output_combo.currentText())
            self.audio_engine.set_devices(
                self.input_combo.currentData() or "",
                self.output_combo.currentData() or "",
            )

        self.device_status_label.setText(
            self._device_status_text(len(input_devices), len(output_devices))
        )
        self.device_status_label.setToolTip(
            f"Detected {len(input_devices)} microphone inputs and {len(output_devices)} monitor outputs. "
            f"{self.audio_engine.virtual_cable_status()}."
        )
        self.update_driver_status()

    def rescan_devices(self) -> None:
        try:
            self.audio_engine.rescan_devices()
        except Exception as exc:
            self.log(f"Could not rescan audio devices: {exc}")
        self.refresh_devices()

    def save_device_selection(self, *_args) -> None:
        sender = self.sender()
        if sender is self.input_combo or not self._preferred_input_missing:
            self.config.selected_input_device_id = self.input_combo.currentData() or ""
            self.config.selected_input_device_name = self.input_combo.currentText().strip()
            self._preferred_input_missing = False
        if sender is self.output_combo or not self._preferred_output_missing:
            self.config.selected_output_device_id = self.output_combo.currentData() or ""
            self.config.selected_output_device_name = self.output_combo.currentText().strip()
            self._preferred_output_missing = False
        self.input_combo.setToolTip(self.input_combo.currentText())
        self.output_combo.setToolTip(self.output_combo.currentText())
        self.audio_engine.set_devices(
            self.input_combo.currentData() or "",
            self.output_combo.currentData() or "",
        )
        self._save_config_safely("audio device selection")

    def save_monitor_mute(self, muted: bool) -> None:
        self.config.monitor_muted = muted
        self.audio_engine.set_monitor_muted(muted)
        self._save_config_safely("local playback mute")

    def save_playback_volume(self, value: int) -> None:
        self.config.playback_volume_percent = max(0, min(200, int(value)))
        self.playback_volume_value_label.setText(self._playback_volume_label_text())
        self.audio_engine.set_playback_volume_percent(self.config.playback_volume_percent)
        self._save_config_safely("local playback volume")

    def save_raw_hd_soundboard_mode(self, enabled: bool) -> None:
        self.config.raw_hd_soundboard_enabled = enabled
        self.audio_engine.set_raw_hd_soundboard(enabled)
        self._save_config_safely("HD soundboard mode")

    def save_mic_noise_gate(self, *_args) -> None:
        enabled = self.mic_gate_checkbox.isChecked()
        threshold_db = float(self.mic_gate_slider.value())
        self.config.mic_noise_gate_enabled = enabled
        self.config.mic_noise_gate_threshold_db = threshold_db
        self.mic_gate_slider.setEnabled(enabled)
        self.mic_gate_value_label.setText(self._mic_gate_label_text())
        self.audio_engine.set_mic_noise_gate(enabled, threshold_db)
        self._save_config_safely("microphone noise gate")

    def refresh_pad_buttons(self) -> None:
        while self.pad_grid.count():
            item = self.pad_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.pad_selector.blockSignals(True)
        self.pad_selector.clear()
        self.pad_buttons.clear()

        for index, pad in enumerate(self.config.pads):
            button = PadButton(pad.id)
            button.clicked_for_edit.connect(self.select_pad)
            self._apply_pad_button_state(button, pad)
            row = index // PAD_GRID_COLUMNS
            column = index % PAD_GRID_COLUMNS
            self.pad_grid.addWidget(button, row, column)
            self.pad_buttons[pad.id] = button
            pad_number = pad.id.removeprefix("pad-").upper()
            self.pad_selector.addItem(f"Pad {pad_number}: {pad.label}", pad.id)

        self.pad_selector.blockSignals(False)
        self._set_selected_pad_ui(self.selected_pad_id)
        self.remote_server.broadcast_state()

    def _apply_pad_button_state(self, button: PadButton, pad: PadConfig) -> None:
        button.set_pad_display(pad)

    def select_pad(self, pad_id: str) -> None:
        self.selected_pad_id = pad_id
        self.refresh_editor()
        self._set_selected_pad_ui(pad_id)

    def _set_selected_pad_ui(self, pad_id: str) -> None:
        for button_pad_id, button in self.pad_buttons.items():
            button.setChecked(button_pad_id == pad_id)
        combo_index = self.pad_selector.findData(pad_id)
        if combo_index >= 0:
            self.pad_selector.setCurrentIndex(combo_index)

    def _sync_selected_from_combo(self) -> None:
        pad_id = self.pad_selector.currentData()
        if isinstance(pad_id, str) and pad_id:
            self.select_pad(pad_id)

    def refresh_editor(self) -> None:
        pad = self.current_pad()
        self.label_edit.setText(pad.label)
        self.color_edit.setText(pad.color_hex)
        self._editor_single_sound_path = pad.sound_path
        self._editor_pack_path = pad.sound_pack_path
        self._editor_sound_mode = self._normalize_sound_mode(pad.sound_mode)
        self.sound_mode_combo.blockSignals(True)
        mode_index = self.sound_mode_combo.findData(self._editor_sound_mode)
        self.sound_mode_combo.setCurrentIndex(max(0, mode_index))
        self.sound_mode_combo.blockSignals(False)
        self.sound_edit.setText(self._active_editor_sound_path())
        self._update_sound_mode_controls()
        self.image_edit.setText(pad.image_path)
        self.hotkey_edit.setKeySequence(
            QKeySequence.fromString(pad.hotkey, QKeySequence.SequenceFormat.PortableText)
        )
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(max(0, min(200, int(pad.volume_percent))))
        self.volume_slider.blockSignals(False)
        self.update_volume_label()
        self.editor_status_label.setText(f"Editing {pad.id}")

    def save_selected_pad(self) -> None:
        if not self._validate_editor_sound_for_user_action():
            return
        if not self._validate_editor_hotkey():
            return

        old_assets = self._current_pad_asset_paths()
        managed_before = {path.resolve(strict=False) for path in self.asset_store.managed_asset_files()}
        pad_index = next(index for index, pad in enumerate(self.config.pads) if pad.id == self.selected_pad_id)
        original_pad = replace(self.config.pads[pad_index])
        try:
            pad = self._apply_editor_to_current_pad()
            self.config_manager.save(self.config)
        except Exception as exc:
            self.config.pads[pad_index] = original_pad
            self.audio_engine.set_pad_volume_percent(original_pad.id, original_pad.volume_percent)
            new_assets = [
                path
                for path in self.asset_store.managed_asset_files()
                if path.resolve(strict=False) not in managed_before
            ]
            self.refresh_pad_buttons()
            self.refresh_editor()
            self._cleanup_unused_runtime_assets(new_assets)
            self.log(f"Could not save {original_pad.id}: {exc}")
            return
        self.refresh_pad_buttons()
        self.refresh_editor()
        self.audio_engine.set_pad_volume_percent(pad.id, pad.volume_percent)
        self.audio_engine.preload_pads([pad])
        hotkey_failures = self.refresh_hotkeys()
        self._cleanup_unused_runtime_assets(old_assets)
        self.log(f"Saved {pad.id}.")
        if pad.id in hotkey_failures:
            SoundValidationDialog(
                self,
                f"{pad.hotkey} was saved but could not be activated. {hotkey_failures[pad.id]}",
                "Shortcut unavailable",
            ).exec()

    def _apply_editor_to_current_pad(self) -> PadConfig:
        pad = self.current_pad()
        self._store_active_editor_sound_path()
        pad.label = (self.label_edit.text().strip() or pad.id)[:48]
        pad.color_hex = self._normalize_hex(self.color_edit.text())
        sound_mode = self._normalize_sound_mode(self.sound_mode_combo.currentData())
        if sound_mode == "pack":
            sound_pack_path = self._copy_sound_pack_to_runtime(self._editor_pack_path, pad.id)
            if sound_pack_path is not None:
                pad.sound_pack_path = sound_pack_path
        else:
            sound_path = self._copy_sound_to_runtime(self._editor_single_sound_path, pad.id)
            if sound_path is not None:
                pad.sound_path = sound_path
        image_path = self._copy_image_to_runtime(self.image_edit.text().strip(), pad.id)
        pad.sound_mode = sound_mode
        pad.image_path = image_path if image_path is not None else pad.image_path
        pad.volume_percent = self._selected_pad_volume_percent()
        pad.hotkey = normalize_hotkey(
            self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        )
        return pad

    def _validate_editor_hotkey(self) -> bool:
        text = self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        try:
            normalized = normalize_hotkey(text)
            signature = parse_hotkey(normalized) if normalized else None
        except HotkeyError as exc:
            SoundValidationDialog(self, str(exc), "Shortcut not saved").exec()
            return False

        if signature is not None:
            for pad in self.config.pads:
                if pad.id == self.selected_pad_id or not pad.hotkey:
                    continue
                try:
                    if parse_hotkey(pad.hotkey) == signature:
                        SoundValidationDialog(
                            self,
                            f"That shortcut is already assigned to {pad.label}.",
                            "Shortcut not saved",
                        ).exec()
                        return False
                except HotkeyError:
                    continue
        self.hotkey_edit.setKeySequence(
            QKeySequence.fromString(normalized, QKeySequence.SequenceFormat.PortableText)
        )
        return True

    def refresh_hotkeys(self) -> dict[str, str]:
        failures = self.hotkey_manager.register_pads(self.config.pads)
        current_pad = self.current_pad()
        if current_pad.id in failures:
            self.hotkey_edit.setToolTip(f"Shortcut inactive: {failures[current_pad.id]}")
        elif current_pad.hotkey:
            self.hotkey_edit.setToolTip(f"Active globally: {current_pad.hotkey}")
        else:
            self.hotkey_edit.setToolTip(
                "Optional global shortcut. Press one key combination; it is applied automatically."
            )
        for pad_id, reason in failures.items():
            pad = self.find_pad(pad_id)
            binding = pad.hotkey if pad is not None else pad_id
            self.log(f"Shortcut {binding} is inactive: {reason}")
        return failures

    def save_current_hotkey(self) -> None:
        pad = self.current_pad()
        previous_hotkey = pad.hotkey
        if not self._validate_editor_hotkey():
            self.hotkey_edit.setKeySequence(
                QKeySequence.fromString(previous_hotkey, QKeySequence.SequenceFormat.PortableText)
            )
            return

        normalized = normalize_hotkey(
            self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        )
        if normalized == previous_hotkey:
            return

        pad.hotkey = normalized
        try:
            self.config_manager.save(self.config)
        except OSError as exc:
            pad.hotkey = previous_hotkey
            self.hotkey_edit.setKeySequence(
                QKeySequence.fromString(previous_hotkey, QKeySequence.SequenceFormat.PortableText)
            )
            self.log(f"Could not save the shortcut for {pad.label}: {exc}")
            return

        failures = self.refresh_hotkeys()
        if pad.id in failures:
            SoundValidationDialog(
                self,
                f"{normalized} was saved but could not be activated. {failures[pad.id]}",
                "Shortcut unavailable",
            ).exec()
            return

        if normalized:
            self.log(f"Shortcut active for {pad.label}: {normalized}")
        else:
            self.log(f"Shortcut cleared for {pad.label}.")

    def clear_current_hotkey(self) -> None:
        self.hotkey_edit.clear()
        self.save_current_hotkey()

    def export_profile_zip(self) -> None:
        if not self._validate_editor_sound_for_user_action():
            return
        if not self._validate_editor_hotkey():
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Profile ZIP",
            str(self.paths.app_dir / "soundboard-profile.zip"),
            "Profile ZIP (*.zip);;ZIP Files (*.zip)",
        )
        if not path:
            return

        target_path = Path(path)
        if target_path.suffix.lower() != ".zip":
            target_path = target_path.with_suffix(".zip")

        try:
            old_assets = self._current_pad_asset_paths()
            self._apply_editor_to_current_pad()
            self.config_manager.save(self.config)
            self._write_profile_zip(target_path)
            self.refresh_hotkeys()
            self.refresh_pad_buttons()
            self.refresh_editor()
            self._cleanup_unused_runtime_assets(old_assets)
            self.log(f"Profile exported: {target_path}")
        except Exception as exc:
            self.log(f"Could not export profile: {exc}")

    def import_profile_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Profile ZIP",
            str(self.paths.app_dir),
            "Profile ZIP (*.zip);;ZIP Files (*.zip)",
        )
        if not path:
            return

        managed_before = {
            path.resolve(strict=False)
            for path in self.asset_store.managed_asset_files()
        }
        old_assets = self._all_pad_asset_paths()
        try:
            pads, settings = self._read_profile_zip(Path(path))
            normalized_settings = self._normalize_imported_settings(settings)
            imported_config = replace(self.config, pads=pads, **normalized_settings)
            self.config_manager.save(imported_config)
        except Exception as exc:
            imported_orphans = [
                candidate
                for candidate in self.asset_store.managed_asset_files()
                if candidate.resolve(strict=False) not in managed_before
            ]
            self._cleanup_unused_runtime_assets(imported_orphans)
            self.log(f"Could not import profile: {exc}")
            return

        # The profile is committed at this point. Never remove its assets if a
        # later live UI refresh fails; the saved profile must remain usable.
        self.config = imported_config
        self.selected_pad_id = self.config.pads[0].id
        post_commit_errors: list[str] = []
        post_commit_actions = (
            ("settings", lambda: self._apply_imported_settings(normalized_settings)),
            ("pad volumes", lambda: self.audio_engine.set_pad_volumes(self.config.pads)),
            ("pad board", self.refresh_pad_buttons),
            ("pad editor", self.refresh_editor),
            ("sound preload", lambda: self.audio_engine.preload_pads(self.config.pads)),
            ("shortcuts", self.refresh_hotkeys),
            ("phone remote", self.remote_server.broadcast_state),
        )
        for action_name, action in post_commit_actions:
            try:
                action()
            except Exception as exc:
                post_commit_errors.append(f"{action_name}: {exc}")

        self._cleanup_unused_runtime_assets(old_assets)
        self.log(f"Profile imported: {path}")
        if post_commit_errors:
            self.log("Some live controls need an app restart: " + "; ".join(post_commit_errors))

    def _write_profile_zip(self, target_path: Path) -> None:
        payload, assets = self._profile_payload_and_assets()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    PROFILE_MANIFEST_NAME,
                    json.dumps(payload, indent=2),
                )
                for source_path, archive_name in assets:
                    archive.write(source_path, archive_name)
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _profile_payload_and_assets(self) -> tuple[dict, list[tuple[Path, str]]]:
        assets: list[tuple[Path, str]] = []
        source_to_archive: dict[str, str] = {}
        used_archive_names: set[str] = set()

        def add_asset(path_text: str, folder: str) -> str:
            if not path_text:
                return ""
            source_path = Path(path_text)
            if not source_path.is_file():
                return ""
            if folder == "sounds":
                validate_sound_duration(source_path)

            resolved = source_path.resolve()
            source_key = str(resolved).lower()
            existing = source_to_archive.get(source_key)
            if existing is not None:
                return existing

            archive_name = self._unique_archive_asset_name(folder, source_path.name, used_archive_names)
            source_to_archive[source_key] = archive_name
            used_archive_names.add(archive_name.lower())
            assets.append((resolved, archive_name))
            return archive_name

        def add_sound_pack(path_text: str, pad_id: str) -> list[str]:
            if not path_text or not Path(path_text).is_dir():
                return []
            return [
                add_asset(str(sound_path), f"sound-packs/{pad_id}")
                for sound_path in validate_sound_pack(path_text)
            ]

        pads = []
        for pad in self.config.pads:
            pads.append(
                {
                    "id": pad.id,
                    "label": pad.label,
                    "sound_path": add_asset(pad.sound_path, "sounds"),
                    "sound_mode": MainWindow._normalize_sound_mode(pad.sound_mode),
                    "sound_pack_paths": add_sound_pack(pad.sound_pack_path, pad.id),
                    "image_path": add_asset(pad.image_path, "images"),
                    "color_hex": pad.color_hex,
                    "volume_percent": pad.volume_percent,
                    "hotkey": pad.hotkey,
                }
            )

        payload = {
            "profile_version": PROFILE_VERSION,
            "app": "Soundboard Studio",
            "settings": {
                "monitor_muted": self.config.monitor_muted,
                "playback_volume_percent": self.config.playback_volume_percent,
                "mic_noise_gate_enabled": self.config.mic_noise_gate_enabled,
                "mic_noise_gate_threshold_db": self.config.mic_noise_gate_threshold_db,
                "raw_hd_soundboard_enabled": self.config.raw_hd_soundboard_enabled,
                "remote_playback_mode": self.config.remote_playback_mode,
            },
            "pads": pads,
        }
        manifest_size = len(json.dumps(payload, indent=2).encode("utf-8"))
        if manifest_size > PROFILE_MAX_MANIFEST_BYTES:
            raise ValueError("The profile settings are too large to export safely.")
        if len(assets) + 1 > PROFILE_MAX_ENTRIES:
            raise ValueError("The profile contains too many files to export safely.")
        total_size = manifest_size + sum(source.stat().st_size for source, _name in assets)
        if total_size > PROFILE_MAX_TOTAL_BYTES:
            raise ValueError("The profile is too large to export safely.")
        oversized = [source.name for source, _name in assets if source.stat().st_size > PROFILE_MAX_ASSET_BYTES]
        if oversized:
            raise ValueError(f"Profile asset is too large to export: {oversized[0]}")
        return payload, assets

    def _read_profile_zip(self, profile_path: Path) -> tuple[list[PadConfig], dict]:
        with zipfile.ZipFile(profile_path) as archive:
            members = archive.infolist()
            if len(members) > PROFILE_MAX_ENTRIES:
                raise ValueError("The profile contains too many files.")
            if sum(member.file_size for member in members) > PROFILE_MAX_TOTAL_BYTES:
                raise ValueError("The profile is too large after extraction.")
            member_names = [member.filename for member in members]
            names = set(member_names)
            if len(names) != len(member_names):
                raise ValueError("The profile contains duplicate file names.")
            if PROFILE_MANIFEST_NAME not in names:
                raise ValueError(f"{PROFILE_MANIFEST_NAME} was not found in the ZIP.")
            if archive.getinfo(PROFILE_MANIFEST_NAME).file_size > PROFILE_MAX_MANIFEST_BYTES:
                raise ValueError("The profile manifest is too large.")

            payload = json.loads(archive.read(PROFILE_MANIFEST_NAME).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("The profile manifest root must be an object.")
            try:
                profile_version = int(payload.get("profile_version", 1))
            except (TypeError, ValueError):
                raise ValueError("The profile version is invalid.") from None
            if profile_version not in range(1, PROFILE_VERSION + 1):
                raise ValueError(f"Profile version {profile_version} is not supported.")
            raw_pads = payload.get("pads")
            supported_pad_counts = {LEGACY_PAD_COUNT, PAD_COUNT}
            if not isinstance(raw_pads, list) or len(raw_pads) not in supported_pad_counts:
                raise ValueError(
                    f"This profile must contain {LEGACY_PAD_COUNT} or {PAD_COUNT} pads."
                )

            with tempfile.TemporaryDirectory(prefix="profile-import-", dir=self.paths.runtime_dir) as temporary_dir:
                staging_dir = Path(temporary_dir)
                imported_pads: list[PadConfig] = []
                for index, item in enumerate(raw_pads):
                    if not isinstance(item, dict):
                        raise ValueError(f"Pad {index + 1} is not valid.")
                    fallback = self.config.pads[index] if index < len(self.config.pads) else PadConfig(
                        id=f"pad-{index + 1:02d}",
                        label=f"Pad {index + 1}",
                    )
                    pad_id = f"pad-{index + 1:02d}"
                    staged_sound = self._stage_profile_asset(
                        archive,
                        names,
                        str(item.get("sound_path") or ""),
                        staging_dir,
                        asset_kind="sound",
                    )
                    raw_pack_paths = item.get("sound_pack_paths", [])
                    if not isinstance(raw_pack_paths, list):
                        raise ValueError(f"Pad {index + 1} sound pack is not valid.")
                    staged_pack_sounds = [
                        self._stage_profile_asset(
                            archive,
                            names,
                            str(pack_path or ""),
                            staging_dir,
                            asset_kind="sound",
                        )
                        for pack_path in raw_pack_paths
                    ]
                    staged_pack_sounds = [path for path in staged_pack_sounds if path]
                    staged_image = self._stage_profile_asset(
                        archive,
                        names,
                        str(item.get("image_path") or ""),
                        staging_dir,
                        asset_kind="image",
                    )
                    sound_path = str(self.asset_store.copy_sound(staged_sound, pad_id)) if staged_sound else ""
                    sound_pack_path = (
                        str(
                            self.asset_store.copy_sound_pack_files(
                                [Path(path) for path in staged_pack_sounds],
                                pad_id,
                            )
                        )
                        if staged_pack_sounds
                        else ""
                    )
                    image_path = str(self.asset_store.copy_image(staged_image, pad_id)) if staged_image else ""
                    imported_pads.append(
                        PadConfig(
                            id=pad_id,
                            label=str(item.get("label") or fallback.label)[:48],
                            sound_path=sound_path,
                            sound_mode=self._normalize_sound_mode(item.get("sound_mode", "single")),
                            sound_pack_path=sound_pack_path,
                            image_path=image_path,
                            color_hex=self._normalize_hex(str(item.get("color_hex") or fallback.color_hex)),
                            volume_percent=self._clamp_pad_volume(item.get("volume_percent", fallback.volume_percent)),
                            hotkey=self._normalize_imported_hotkey(item.get("hotkey")),
                        )
                    )

                # A 12-pad backup remains fully usable after the 24-pad upgrade.
                # New slots are intentionally blank rather than inheriting unrelated pads.
                imported_pads.extend(default_pads()[len(imported_pads) :])

            settings = payload.get("settings")
            return imported_pads, settings if isinstance(settings, dict) else {}

    def _stage_profile_asset(
        self,
        archive: zipfile.ZipFile,
        archive_names: set[str],
        relative_path: str,
        staging_dir: Path,
        *,
        asset_kind: str,
    ) -> str:
        if not relative_path:
            return ""

        normalized = relative_path.replace("\\", "/")
        relative = Path(normalized)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError(f"Unsafe asset path in profile ZIP: {relative_path}")
        if normalized not in archive_names:
            raise FileNotFoundError(f"Profile asset missing from ZIP: {relative_path}")

        member = archive.getinfo(normalized)
        if member.is_dir() or member.file_size > PROFILE_MAX_ASSET_BYTES:
            raise ValueError(f"Profile asset is invalid or too large: {relative_path}")

        asset_dir = staging_dir / uuid.uuid4().hex
        asset_dir.mkdir(parents=True, exist_ok=True)
        suffix = relative.suffix.lower()
        staged_path = asset_dir / f"asset{suffix}"
        with archive.open(member) as source, staged_path.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)

        if asset_kind == "sound":
            validate_sound_duration(staged_path)
        elif asset_kind == "image" and QImage(str(staged_path)).isNull():
            raise ValueError(f"Could not read profile image: {relative_path}")
        return str(staged_path)

    def _normalize_imported_settings(self, settings: dict) -> dict:
        return {
            "monitor_muted": self._coerce_bool(settings.get("monitor_muted"), self.config.monitor_muted),
            "playback_volume_percent": self._clamp_number(
                settings.get("playback_volume_percent"), 0, 200, self.config.playback_volume_percent, int
            ),
            "mic_noise_gate_enabled": self._coerce_bool(
                settings.get("mic_noise_gate_enabled"), self.config.mic_noise_gate_enabled
            ),
            "mic_noise_gate_threshold_db": self._clamp_number(
                settings.get("mic_noise_gate_threshold_db"),
                -70.0,
                -20.0,
                self.config.mic_noise_gate_threshold_db,
                float,
            ),
            "raw_hd_soundboard_enabled": self._coerce_bool(
                settings.get("raw_hd_soundboard_enabled"), self.config.raw_hd_soundboard_enabled
            ),
            "remote_playback_mode": self._normalize_remote_playback_mode(
                settings.get("remote_playback_mode", self.config.remote_playback_mode)
            ),
        }

    def _apply_imported_settings(self, settings: dict) -> None:
        self.config.monitor_muted = settings["monitor_muted"]
        self.config.playback_volume_percent = settings["playback_volume_percent"]
        self.config.mic_noise_gate_enabled = settings["mic_noise_gate_enabled"]
        self.config.mic_noise_gate_threshold_db = settings["mic_noise_gate_threshold_db"]
        self.config.raw_hd_soundboard_enabled = settings["raw_hd_soundboard_enabled"]
        self.config.remote_playback_mode = settings["remote_playback_mode"]

        self.mute_monitor_checkbox.blockSignals(True)
        self.mute_monitor_checkbox.setChecked(self.config.monitor_muted)
        self.mute_monitor_checkbox.blockSignals(False)
        self.playback_volume_slider.blockSignals(True)
        self.playback_volume_slider.setValue(self.config.playback_volume_percent)
        self.playback_volume_slider.blockSignals(False)
        self.playback_volume_value_label.setText(self._playback_volume_label_text())
        self.raw_hd_checkbox.blockSignals(True)
        self.raw_hd_checkbox.setChecked(self.config.raw_hd_soundboard_enabled)
        self.raw_hd_checkbox.blockSignals(False)
        self.mic_gate_checkbox.blockSignals(True)
        self.mic_gate_checkbox.setChecked(self.config.mic_noise_gate_enabled)
        self.mic_gate_checkbox.blockSignals(False)
        self.mic_gate_slider.blockSignals(True)
        self.mic_gate_slider.setValue(int(round(self.config.mic_noise_gate_threshold_db)))
        self.mic_gate_slider.setEnabled(self.config.mic_noise_gate_enabled)
        self.mic_gate_slider.blockSignals(False)
        self.mic_gate_value_label.setText(self._mic_gate_label_text())

        self.audio_engine.set_monitor_muted(self.config.monitor_muted)
        self.audio_engine.set_playback_volume_percent(self.config.playback_volume_percent)
        self.audio_engine.set_mic_noise_gate(
            self.config.mic_noise_gate_enabled,
            self.config.mic_noise_gate_threshold_db,
        )
        self.audio_engine.set_raw_hd_soundboard(self.config.raw_hd_soundboard_enabled)

    @staticmethod
    def _unique_archive_asset_name(folder: str, filename: str, used_archive_names: set[str]) -> str:
        source_name = Path(filename).name or "asset"
        stem = Path(source_name).stem or "asset"
        suffix = Path(source_name).suffix
        candidate = f"{folder}/{source_name}"
        counter = 2
        while candidate.lower() in used_archive_names:
            candidate = f"{folder}/{stem}-{counter}{suffix}"
            counter += 1
        return candidate

    @staticmethod
    def _clamp_pad_volume(value: object) -> int:
        try:
            volume = int(value)
        except (TypeError, ValueError):
            volume = 100
        return max(0, min(200, volume))

    @staticmethod
    def _normalize_imported_hotkey(value: object) -> str:
        try:
            return normalize_hotkey(value)
        except HotkeyError:
            return ""

    def update_volume_label(self) -> None:
        self.volume_value_label.setText(f"{self._selected_pad_volume_percent()}%")

    def handle_pad_volume_changed(self, value: int) -> None:
        pad = self.current_pad()
        pad.volume_percent = max(0, min(200, int(value)))
        self.update_volume_label()
        self.audio_engine.set_pad_volume_percent(pad.id, pad.volume_percent)
        self._save_config_safely("pad volume")
        self.remote_server.broadcast_state()

    def play_selected_pad(self) -> None:
        if not self._validate_editor_sound_for_user_action():
            return
        self.audio_engine.trigger_pad(self._editor_pad_preview(), route_to_virtual=False)

    def handle_sound_mode_changed(self, *_args) -> None:
        self._store_active_editor_sound_path()
        self._editor_sound_mode = self._normalize_sound_mode(self.sound_mode_combo.currentData())
        self.sound_edit.setText(self._active_editor_sound_path())
        self._update_sound_mode_controls()

    def handle_remote_trigger(self, pad_id: str) -> None:
        pad = self.find_pad(pad_id)
        if pad is None:
            self.log(f"Phone remote asked for an unknown pad: {pad_id}")
            return
        if self.config.remote_playback_mode == "replace":
            self.audio_engine.stop_all_sounds()
        self.audio_engine.trigger_pad(pad)

    def handle_hotkey_trigger(self, pad_id: str) -> None:
        self.handle_remote_trigger(pad_id)

    def handle_remote_stop_all(self) -> None:
        self.audio_engine.stop_all_sounds()

    def handle_remote_playback_mode_changed(self, mode: str) -> None:
        normalized = self._normalize_remote_playback_mode(mode)
        if self.config.remote_playback_mode == normalized:
            return
        self.config.remote_playback_mode = normalized
        self._save_config_safely("phone playback mode")
        self.remote_server.broadcast_state()

    def browse_sound_source(self) -> None:
        if self._normalize_sound_mode(self.sound_mode_combo.currentData()) == "pack":
            self.browse_sound_pack_folder()
        else:
            self.browse_sound_file()

    def browse_sound_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Sound File",
            str(self.paths.sounds_dir),
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*.*)",
        )
        if path:
            self._open_sound_clip_editor(path)

    def browse_sound_pack_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose Sound Pack Folder",
            str(self.paths.sounds_dir),
        )
        if not path:
            return

        old_editor_asset = self._editor_pack_path
        managed_path = self._copy_sound_pack_to_runtime(
            path,
            self.current_pad().id,
            allow_partial=True,
        )
        if managed_path is None:
            return
        self._editor_pack_path = managed_path
        self.sound_edit.setText(managed_path)
        self._update_sound_mode_controls()
        self._cleanup_unused_runtime_assets([old_editor_asset])

    def edit_sound_file(self) -> None:
        self._open_sound_clip_editor(self.sound_edit.text().strip())

    def _open_sound_clip_editor(self, initial_path: str = "") -> bool:
        dialog = SoundClipEditorDialog(
            self,
            initial_path,
            preview_callback=self._preview_clip_file,
            stop_preview_callback=self._stop_clip_preview,
            preview_position_callback=self._clip_preview_position,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_path:
                return False

            old_editor_asset = self.sound_edit.text().strip()
            managed_path = self._copy_sound_to_runtime(dialog.result_path, self.current_pad().id)
            if managed_path is None:
                return False
            self.sound_edit.setText(managed_path)
            self._editor_single_sound_path = managed_path
            self._cleanup_unused_runtime_assets([old_editor_asset])
            return True
        finally:
            dialog.cleanup()

    def _preview_clip_file(self, path: str) -> None:
        self._stop_clip_preview()
        preview = PadConfig(
            id="__clip-editor-preview__",
            label="Clip preview",
            sound_path=path,
            volume_percent=100,
        )
        self.audio_engine.trigger_pad(preview, route_to_virtual=False, log_action=False)

    def _stop_clip_preview(self) -> None:
        self.audio_engine.stop_pad_sounds("__clip-editor-preview__")

    def _clip_preview_position(self) -> float | None:
        return self.audio_engine.pad_playback_position_seconds("__clip-editor-preview__")

    def browse_image_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Button Image",
            str(self.paths.images_dir),
            "Image Files (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*.*)",
        )
        if path:
            old_editor_asset = self.image_edit.text().strip()
            managed_path = self._copy_image_to_runtime(path, self.current_pad().id)
            if managed_path is None:
                return
            self.image_edit.setText(managed_path or path)
            self._cleanup_unused_runtime_assets([old_editor_asset])

    def ensure_runtime_assets(self) -> None:
        changed = False
        for pad in self.config.pads:
            recovered_sound = self._recover_moved_runtime_asset(pad.sound_path, self.paths.sounds_dir)
            if recovered_sound != pad.sound_path:
                pad.sound_path = recovered_sound
                changed = True
            sound_path = self._copy_sound_to_runtime(pad.sound_path, pad.id, log_errors=False)
            if sound_path is not None and sound_path != pad.sound_path:
                pad.sound_path = sound_path
                changed = True

            recovered_pack = self._recover_moved_runtime_asset(pad.sound_pack_path, self.paths.sounds_dir)
            if recovered_pack != pad.sound_pack_path:
                pad.sound_pack_path = recovered_pack
                changed = True
            pack_path = self._copy_sound_pack_to_runtime(pad.sound_pack_path, pad.id, log_errors=False)
            if pack_path is not None and pack_path != pad.sound_pack_path:
                pad.sound_pack_path = pack_path
                changed = True

            recovered_image = self._recover_moved_runtime_asset(pad.image_path, self.paths.images_dir)
            if recovered_image != pad.image_path:
                pad.image_path = recovered_image
                changed = True
            image_path = self._copy_image_to_runtime(pad.image_path, pad.id, log_errors=False)
            if image_path is not None and image_path != pad.image_path:
                pad.image_path = image_path
                changed = True

        if changed:
            self._save_config_safely("managed pad files")
            self.refresh_editor()
            self.refresh_pad_buttons()
        self._cleanup_unused_runtime_assets()

    @staticmethod
    def _recover_moved_runtime_asset(path_text: str, target_dir: Path) -> str:
        if not path_text:
            return ""

        original = Path(path_text).expanduser()
        if original.is_file():
            return str(original.resolve())

        parts = list(original.parts)
        lowered = [part.casefold() for part in parts]
        target_name = target_dir.name.casefold()
        for index in range(len(parts) - 2, -1, -1):
            if lowered[index] != "runtime" or lowered[index + 1] != target_name:
                continue
            relative_parts = parts[index + 2 :]
            if not relative_parts:
                return path_text
            candidate = target_dir.joinpath(*relative_parts)
            if candidate.is_file() or candidate.is_dir():
                return str(candidate.resolve())
        return path_text

    def _copy_sound_to_runtime(self, path_text: str, pad_id: str, *, log_errors: bool = True) -> str | None:
        return self._copy_asset_to_runtime(path_text, pad_id, "sound", log_errors)

    def _copy_sound_pack_to_runtime(
        self,
        path_text: str,
        pad_id: str,
        *,
        log_errors: bool = True,
        allow_partial: bool = False,
    ) -> str | None:
        if not path_text:
            return ""
        try:
            rejected: tuple[RejectedSoundPackFile, ...] = ()
            if allow_partial:
                scan = scan_sound_pack(path_text)
                files = list(scan.accepted)
                rejected = scan.rejected
                if not files:
                    raise SoundValidationError(
                        self._sound_pack_skip_message(
                            Path(path_text),
                            rejected,
                            pack_added=False,
                        )
                    )
            else:
                files = validate_sound_pack(path_text)
            managed_path = self.asset_store.copy_sound_pack(path_text, pad_id, files)
            validate_sound_pack(managed_path)
            if rejected and log_errors:
                self._show_sound_validation_warning(
                    self._sound_pack_skip_message(Path(path_text), rejected),
                    "Some sounds were skipped",
                )
            return str(managed_path)
        except FileNotFoundError:
            if log_errors:
                self.log(f"Sound pack folder not found. Keeping the previous pack: {path_text}")
            return None
        except SoundValidationError as exc:
            if log_errors:
                self._show_sound_validation_warning(str(exc))
            return None
        except Exception as exc:
            if log_errors:
                self.log(f"Could not copy the sound pack into the app folder: {exc}")
            return None

    def _copy_image_to_runtime(self, path_text: str, pad_id: str, *, log_errors: bool = True) -> str | None:
        return self._copy_asset_to_runtime(path_text, pad_id, "image", log_errors)

    def _copy_asset_to_runtime(
        self,
        path_text: str,
        pad_id: str,
        asset_kind: str,
        log_errors: bool,
    ) -> str | None:
        if not path_text:
            return ""

        try:
            if asset_kind == "sound":
                validate_sound_duration(path_text)
                return str(self.asset_store.copy_sound(path_text, pad_id))
            source_path = Path(path_text).expanduser()
            if source_path.is_file() and QImage(str(source_path)).isNull():
                if log_errors:
                    self.log(f"Could not read this button image: {source_path.name}")
                return None
            return str(self.asset_store.copy_image(path_text, pad_id))
        except FileNotFoundError:
            if log_errors:
                self.log(f"{asset_kind.title()} file not found. Keeping the previous pad file: {path_text}")
            return None
        except SoundValidationError as exc:
            if log_errors:
                self._show_sound_validation_warning(str(exc))
            return None
        except Exception as exc:
            if log_errors:
                self.log(f"Could not copy {asset_kind} into the app folder: {exc}")
            return None

    def _validate_editor_sound_for_user_action(self) -> bool:
        if self._normalize_sound_mode(self.sound_mode_combo.currentData()) == "pack":
            path_text = self.sound_edit.text().strip()
            if not path_text:
                return True
            try:
                validate_sound_pack(path_text)
                return True
            except FileNotFoundError:
                return True
            except SoundValidationError as exc:
                self._show_sound_validation_warning(str(exc))
                return False

        path_text = self.sound_edit.text().strip()
        if not path_text:
            return True

        try:
            validate_sound_duration(path_text)
            return True
        except FileNotFoundError:
            return True
        except SoundValidationError:
            return self._open_sound_clip_editor(path_text)

    @staticmethod
    def _sound_pack_skip_message(
        source_directory: Path,
        rejected: tuple[RejectedSoundPackFile, ...],
        *,
        pack_added: bool = True,
    ) -> str:
        source_directory = source_directory.resolve()
        names: list[str] = []
        for item in rejected:
            try:
                name = item.path.resolve().relative_to(source_directory).as_posix()
            except ValueError:
                name = item.path.name
            names.append(f"- {name}")
        count = len(names)
        reason = "they are" if count != 1 else "it is"
        outcome = (
            f"Sound pack added, but {count} file{'s were' if count != 1 else ' was'} skipped"
            if pack_added
            else "Sound pack not added because no usable files remained"
        )
        return (
            f"{outcome}. These files were excluded because {reason} unreadable or longer than "
            "15 seconds:\n"
            + "\n".join(names)
        )

    def _show_sound_validation_warning(
        self,
        message: str,
        title: str = "Sound not accepted",
    ) -> None:
        self.log(message)
        SoundValidationDialog(self, message, title).exec()

    def _current_pad_asset_paths(self) -> list[str]:
        pad = self.current_pad()
        return [pad.sound_path, pad.sound_pack_path, pad.image_path]

    def _all_pad_asset_paths(self) -> list[str]:
        paths: list[str] = []
        for pad in self.config.pads:
            paths.extend([pad.sound_path, pad.sound_pack_path, pad.image_path])
        return paths

    def _referenced_runtime_assets(self) -> set[Path]:
        referenced: set[Path] = set()

        def add_path(path_text: str) -> None:
            if path_text and self.asset_store.is_managed_asset(path_text):
                path = Path(path_text).resolve(strict=False)
                referenced.add(path)
                if path.is_dir():
                    referenced.update(candidate.resolve(strict=False) for candidate in path.rglob("*") if candidate.is_file())

        for pad in self.config.pads:
            add_path(pad.sound_path)
            add_path(pad.sound_pack_path)
            add_path(pad.image_path)

        if hasattr(self, "sound_edit"):
            add_path(self.sound_edit.text().strip())
            add_path(getattr(self, "_editor_single_sound_path", ""))
            add_path(getattr(self, "_editor_pack_path", ""))
        if hasattr(self, "image_edit"):
            add_path(self.image_edit.text().strip())

        return referenced

    def _cleanup_unused_runtime_assets(self, candidates: Iterable[str | Path] | None = None) -> None:
        if candidates is None:
            asset_paths = self.asset_store.managed_asset_files()
        else:
            asset_paths = [Path(path) for path in candidates if str(path)]

        referenced = self._referenced_runtime_assets()
        seen: set[Path] = set()
        for candidate_path in asset_paths:
            resolved = candidate_path.resolve(strict=False)
            contains_reference = resolved.is_dir() and any(
                reference.is_relative_to(resolved) for reference in referenced
            )
            if resolved in seen or resolved in referenced or contains_reference:
                continue
            seen.add(resolved)
            try:
                self.asset_store.remove_if_managed(str(resolved))
            except OSError:
                pass

    def update_client_count(self, count: int) -> None:
        self.remote_client_label.setText(f"Phones connected: {count}")

    def update_remote_urls(self, urls: list[str]) -> None:
        self.page_urls_label.setText("\n".join(urls))
        if self.remote_server.status in {"starting", "running"}:
            self.update_remote_card()

    def update_remote_card(self) -> None:
        if self.config.remote_connection_mode == "internet":
            public_url = self.internet_tunnel.public_url
            if not public_url or not self._remote_access_token:
                self.qr_label.clear()
                if self.internet_tunnel.status == "failed":
                    self.remote_url_label.setText("Internet link unavailable. Press Restart to retry.")
                else:
                    self.remote_url_label.setText("Creating a private Internet link...")
                self.remote_url_label.setToolTip("")
                return
            # Keep the session key in the URL path. Mobile QR scanners are more
            # reliable with paths than query strings, which some handoffs strip.
            private_url = f"{public_url}/connect/{self._remote_access_token}"
            self.remote_url_label.setText(public_url)
            self.remote_url_label.setToolTip("The QR includes a private session key. Share it only with people you trust.")
            self.qr_label.setPixmap(self._qr_pixmap(private_url))
            return

        primary_url = self.remote_server.primary_page_url()
        self.remote_url_label.setText(primary_url)
        self.remote_url_label.setToolTip("Open this address from the same local network.")
        pixmap = self._qr_pixmap(primary_url)
        self.qr_label.setPixmap(pixmap)

    def update_remote_status(self, status: str, detail: str = "") -> None:
        if status == "running":
            self.update_remote_card()
            if self.config.remote_connection_mode == "internet":
                self.server_status_label.setText("Internet remote: preparing secure link")
                self.server_status_label.setToolTip("The local remote is ready; the private Internet link is opening.")
                self._start_internet_tunnel_if_needed()
            else:
                self.server_status_label.setText(f"Local remote ready on port {self.config.server_port}")
                self.server_status_label.setToolTip("Phone remote is available on the local network.")
            self.start_server_button.setText("Restart")
            self.start_server_button.setEnabled(True)
            self.stop_server_button.setText("Stop")
            self.stop_server_button.setEnabled(True)
            return

        if status == "starting":
            mode_label = "Internet" if self.config.remote_connection_mode == "internet" else "Local"
            self.server_status_label.setText(f"{mode_label} remote starting on port {self.config.server_port}")
            self.server_status_label.setToolTip("Phone remote is opening.")
            self.start_server_button.setText("Starting")
            self.start_server_button.setEnabled(False)
            self.stop_server_button.setText("Stop")
            self.stop_server_button.setEnabled(False)
            return

        self.qr_label.clear()
        self.remote_client_label.setText("Phones connected: 0")
        self.start_server_button.setText("Start")
        self.start_server_button.setEnabled(True)
        self.stop_server_button.setText("Stop")
        self.stop_server_button.setEnabled(False)

        if status == "failed":
            self.internet_tunnel.stop()
            message = f"Remote failed on port {self.config.server_port}"
            if detail:
                message = f"{message}: {detail}"
            self.remote_url_label.setText("Remote is offline. Press Start to try again.")
            self.server_status_label.setText(f"Remote failed on port {self.config.server_port}")
            self.server_status_label.setToolTip(message)
            return

        self.remote_url_label.setText("Remote is offline. Press Start to reopen it.")
        self.server_status_label.setText("Remote stopped")
        self.server_status_label.setToolTip("Phone remote is offline.")

    def handle_remote_connection_mode_changed(self, *_args) -> None:
        mode = self._normalize_remote_connection_mode(self.remote_mode_combo.currentData())
        self.config.remote_connection_mode = mode
        self._save_config_safely("remote connection mode")
        self.internet_tunnel.stop()
        self._remote_access_token = ""
        self._prepare_remote_access()
        if self.remote_server.status == "running":
            if mode == "internet":
                self._start_internet_tunnel_if_needed()
            else:
                self.update_remote_card()
                self.server_status_label.setText(f"Local remote ready on port {self.config.server_port}")
        elif not self.remote_server.is_active():
            self.remote_server.start()

    def _prepare_remote_access(self) -> None:
        if self.config.remote_connection_mode == "internet":
            if not self._remote_access_token:
                self._remote_access_token = secrets.token_urlsafe(24)
            self.remote_server.set_access_token(self._remote_access_token)
            return
        self._remote_access_token = ""
        self.remote_server.set_access_token(None)

    def _start_internet_tunnel_if_needed(self) -> None:
        if self.config.remote_connection_mode != "internet":
            return
        self._prepare_remote_access()
        if self.internet_tunnel.status in {"starting", "running"}:
            return
        self.internet_tunnel.start(self.config.server_port)
        self.update_remote_card()

    def update_internet_tunnel_status(self, status: str, detail: str = "") -> None:
        if self.config.remote_connection_mode != "internet":
            return
        if status == "running":
            self.server_status_label.setText("Internet remote ready")
            self.server_status_label.setToolTip("The private HTTPS remote is online.")
            self.update_remote_card()
            return
        if status == "starting":
            self.server_status_label.setText("Internet remote: creating secure link")
            self.server_status_label.setToolTip("Preparing a temporary private HTTPS address.")
            self.update_remote_card()
            return
        if status == "failed":
            self.qr_label.clear()
            self.remote_url_label.setText("Internet link unavailable. Press Restart to retry.")
            self.server_status_label.setText("Internet remote failed")
            self.server_status_label.setToolTip(detail or "Could not create the Internet link.")

    def update_mixer_status(self, status: str) -> None:
        full_status = f"Audio route: {status}"
        self.virtual_mic_status_label.setText(self._compact_mixer_status(status))
        self.virtual_mic_status_label.setToolTip(full_status)

    def update_driver_status(self) -> bool:
        cable_available = self.audio_engine.virtual_cable_available()
        if cable_available:
            message = "Virtual mic route: CABLE Output is available in voice apps"
        else:
            message = "Virtual mic route is not installed"
        self.driver_status_label.setText(self._compact_driver_status(message))
        self.driver_status_label.setToolTip(message)
        self._update_virtual_mic_button(cable_available)
        return cable_available

    def _update_virtual_mic_button(self, route_ready: bool) -> None:
        if not hasattr(self, "install_cable_button"):
            return
        if route_ready:
            self.install_cable_button.hide()
            self.install_cable_button.setEnabled(False)
            self.install_cable_button.setToolTip("A virtual mic route is already ready.")
            return

        self.install_cable_button.show()
        self.install_cable_button.setEnabled(not self._auto_cable_install_started)
        if self._auto_cable_install_started:
            self.install_cable_button.setText("Setup running")
            self.install_cable_button.setToolTip("Virtual mic setup has already started. Finish the installer, then refresh devices.")
        else:
            self.install_cable_button.setText("Install virtual mic")
            self.install_cable_button.setToolTip("Install the signed CABLE route so voice apps can select the mixed mic output.")

    def ensure_virtual_mic(self) -> None:
        if self.audio_engine.virtual_cable_available():
            self.log(self.audio_engine.virtual_cable_status())
            self.update_driver_status()
            return

        if self._auto_cable_install_started:
            self.log("Virtual microphone setup is already running for this session.")
            self.update_driver_status()
            return

        self.log("No selectable virtual microphone route is installed yet.")
        self.install_signed_cable(auto=True)
        self.update_driver_status()

    def handle_restart_remote(self) -> None:
        self.internet_tunnel.stop()
        self._remote_access_token = ""
        self._prepare_remote_access()
        if self.remote_server.is_active():
            self.remote_server.stop()
        self.remote_server.start()

    def handle_stop_remote(self) -> None:
        self.internet_tunnel.stop()
        self.remote_server.stop()
        self.log("Phone remote stopped.")

    def handle_install_signed_cable(self) -> None:
        self.install_signed_cable(auto=False)

    def install_signed_cable(self, *, auto: bool) -> bool:
        if self._auto_cable_install_started:
            self.log("Virtual microphone setup is already running.")
            return False

        self._auto_cable_install_started = True
        self._update_virtual_mic_button(False)
        try:
            setup_path = self._prepare_signed_cable_package()
            script_path = self._write_signed_cable_install_script(setup_path)
            if auto:
                self.log("First launch setup: installing the signed virtual mic route.")
            self.log("Windows will ask for administrator permission to install the virtual audio driver.")
            self._launch_hidden_elevated_powershell(script_path)
            self.log("After setup, reboot if Windows asks. Then select CABLE Output in Discord or TeamSpeak.")
            QTimer.singleShot(SIGNED_CABLE_RECHECK_DELAY_MS, self._finish_virtual_mic_setup_check)
            return True
        except Exception as exc:
            self._auto_cable_install_started = False
            self._update_virtual_mic_button(False)
            self.log(f"Could not start signed virtual mic setup: {exc}")
            return False

    def _finish_virtual_mic_setup_check(self) -> None:
        if self._shutdown_complete:
            return
        route_ready = False
        try:
            self.audio_engine.rescan_devices()
            self.refresh_devices()
        except Exception as exc:
            self.log(f"Could not refresh devices after virtual mic setup: {exc}")
        finally:
            self._auto_cable_install_started = False
            route_ready = self.update_driver_status()

        if not route_ready:
            self.log("Virtual mic setup did not finish. You can press Install virtual mic to try again.")

    def _prepare_signed_cable_package(self) -> Path:
        package_dir = self.paths.app_dir / "driver" / "signed_cable_fallback"
        package_dir.mkdir(parents=True, exist_ok=True)
        zip_path = package_dir / SIGNED_CABLE_ZIP_NAME
        extract_dir = package_dir / SIGNED_CABLE_EXTRACT_DIR
        setup_path = extract_dir / SIGNED_CABLE_SETUP_EXE

        if setup_path.is_file() and is_authenticode_signature_valid(setup_path):
            return setup_path

        if extract_dir.exists():
            shutil.rmtree(extract_dir)

        for attempt in range(2):
            if not zip_path.is_file():
                self._download_signed_cable_package(zip_path)
            self.log("Extracting the virtual mic package beside the EXE.")
            try:
                self._extract_signed_cable_package(zip_path, extract_dir)
            except (OSError, ValueError, zipfile.BadZipFile):
                if attempt > 0:
                    raise
                shutil.rmtree(extract_dir, ignore_errors=True)
                zip_path.unlink(missing_ok=True)
                continue

            if setup_path.is_file() and is_authenticode_signature_valid(setup_path):
                return setup_path
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)

        raise RuntimeError("The virtual mic installer could not be repaired or verified.")

    @staticmethod
    def _extract_signed_cable_package(zip_path: Path, extract_dir: Path) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            if sum(member.file_size for member in members) > SIGNED_CABLE_MAX_EXTRACTED_BYTES:
                raise ValueError("The virtual mic package is unexpectedly large after extraction.")
            for member in members:
                normalized = Path(member.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise ValueError("The virtual mic package contains an unsafe file path.")
            archive.extractall(extract_dir)

    def _download_signed_cable_package(self, zip_path: Path) -> None:
        self.log("Downloading the official signed VB-CABLE package.")
        partial_path = zip_path.with_suffix(zip_path.suffix + ".download")
        try:
            request = urllib.request.Request(SIGNED_CABLE_DOWNLOAD_URL, headers={"User-Agent": "Soundboard Studio"})
            with urllib.request.urlopen(request, timeout=60) as source, partial_path.open("wb") as destination:
                downloaded = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > SIGNED_CABLE_MAX_DOWNLOAD_BYTES:
                        raise ValueError("The virtual mic package download is unexpectedly large.")
                    destination.write(chunk)
            if partial_path.stat().st_size == 0 or not zipfile.is_zipfile(partial_path):
                raise OSError("The virtual mic package download was empty.")
            partial_path.replace(zip_path)
        finally:
            partial_path.unlink(missing_ok=True)

    def _write_signed_cable_install_script(self, setup_path: Path) -> Path:
        package_dir = setup_path.parent
        script_path = package_dir.parent / "install_signed_virtual_microphone.ps1"
        inf_path = package_dir / SIGNED_CABLE_INF
        devcon_path = self.paths.app_dir / "driver" / "tools" / "devcon.exe"
        script = f"""$ErrorActionPreference = 'Stop'
$logPath = Join-Path $PSScriptRoot 'install_signed_virtual_microphone.log'
function Write-InstallLog([string]$Message) {{
    $line = '[{{0}}] {{1}}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}}

try {{
    Write-InstallLog 'Starting signed virtual microphone install.'
    $existing = Get-PnpDevice -Class MEDIA -ErrorAction SilentlyContinue | Where-Object {{ $_.FriendlyName -like '*VB-Audio Virtual Cable*' }}
    if ($existing) {{
        Write-InstallLog 'VB-Audio Virtual Cable is already installed.'
        exit 0
    }}

    $infPath = {self._ps_quote(str(inf_path))}
    $setupPath = {self._ps_quote(str(setup_path))}
    $devconPath = {self._ps_quote(str(devcon_path))}
    $hardwareId = {self._ps_quote(SIGNED_CABLE_HARDWARE_ID)}

    if ((Test-Path $devconPath) -and (Test-Path $infPath)) {{
        Write-InstallLog 'Installing signed cable driver with bundled devcon.'
        & $devconPath install $infPath $hardwareId *> $null
        $devconExit = $LASTEXITCODE
        pnputil /scan-devices *> $null
        Start-Sleep -Seconds 2
        $installed = Get-PnpDevice -Class MEDIA -ErrorAction SilentlyContinue | Where-Object {{ $_.FriendlyName -like '*VB-Audio Virtual Cable*' }}
        if ($devconExit -eq 0 -and $installed) {{
            Write-InstallLog 'Signed cable driver installed with devcon.'
            exit 0
        }}
        Write-InstallLog "devcon install did not finish cleanly. Exit code: $devconExit. Falling back to vendor setup."
    }} else {{
        Write-InstallLog 'Bundled devcon or INF missing. Falling back to vendor setup.'
    }}

    Write-InstallLog 'Launching official VB-CABLE setup.'
    Start-Process -FilePath $setupPath -Wait
    Write-InstallLog 'Official VB-CABLE setup closed.'
    exit 0
}} catch {{
    Write-InstallLog ('ERROR: ' + $_.Exception.Message)
    try {{
        Start-Process -FilePath {self._ps_quote(str(setup_path))} -Wait
    }} catch {{
        Write-InstallLog ('SETUP FALLBACK ERROR: ' + $_.Exception.Message)
    }}
    exit 1
}}
"""
        script_path.write_text(script, encoding="utf-8")
        return script_path

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _cmd_quote(value: str) -> str:
        return '"' + value.replace('"', r'\"') + '"'

    def _launch_hidden_elevated_powershell(self, script_path: Path) -> None:
        arguments = (
            "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
            f"-File {self._cmd_quote(str(script_path))}"
        )
        if sys.platform == "win32":
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                "powershell.exe",
                arguments,
                str(self.paths.app_dir),
                0,
            )
            if int(result) <= 32:
                raise OSError(f"Windows refused to start the elevated installer. ShellExecute code: {result}")
            return

        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=str(self.paths.app_dir),
        )

    def remote_state(self) -> dict:
        return {
            "audioReady": self.audio_engine.audio_ready(),
            "activePadIds": self.audio_engine.active_remote_pad_ids(),
            "playbackMode": self._normalize_remote_playback_mode(self.config.remote_playback_mode),
            "pads": [
                {
                    "id": pad.id,
                    "label": pad.label,
                    "colorHex": pad.color_hex,
                    "volumePercent": pad.volume_percent,
                    "imageUrl": self._pad_image_url(pad),
                }
                for pad in self.config.pads
            ]
        }

    @staticmethod
    def _pad_image_url(pad: PadConfig) -> str | None:
        if not pad.image_path:
            return None
        image_path = Path(pad.image_path)
        if not image_path.is_file():
            return None
        try:
            stat = image_path.stat()
            version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
        except OSError:
            version = "0"
        return f"/pad-image/{pad.id}?v={version}"

    def resolve_pad_image(self, pad_id: str) -> Path | None:
        pad = self.find_pad(pad_id)
        if pad is None or not pad.image_path:
            return None
        image_path = Path(pad.image_path)
        return image_path if image_path.is_file() else None

    def current_pad(self) -> PadConfig:
        return self.find_pad(self.selected_pad_id) or self.config.pads[0]

    def _editor_pad_preview(self) -> PadConfig:
        pad = self.current_pad()
        self._store_active_editor_sound_path()
        return PadConfig(
            id=pad.id,
            label=self.label_edit.text().strip() or pad.id,
            sound_path=self._editor_single_sound_path,
            sound_mode=self._normalize_sound_mode(self.sound_mode_combo.currentData()),
            sound_pack_path=self._editor_pack_path,
            image_path=self.image_edit.text().strip(),
            color_hex=self._normalize_hex(self.color_edit.text()),
            volume_percent=self._selected_pad_volume_percent(),
            hotkey=self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
        )

    def find_pad(self, pad_id: str) -> PadConfig | None:
        for pad in self.config.pads:
            if pad.id == pad_id:
                return pad
        return None

    def _active_editor_sound_path(self) -> str:
        return self._editor_pack_path if self._editor_sound_mode == "pack" else self._editor_single_sound_path

    def _store_active_editor_sound_path(self) -> None:
        if not hasattr(self, "sound_edit"):
            return
        path_text = self.sound_edit.text().strip()
        if self._editor_sound_mode == "pack":
            self._editor_pack_path = path_text
        else:
            self._editor_single_sound_path = path_text

    def _update_sound_mode_controls(self) -> None:
        is_pack = self._editor_sound_mode == "pack"
        self.sound_edit.setReadOnly(is_pack)
        self.sound_edit.setPlaceholderText(
            "Choose a folder of audio clips" if is_pack else "Choose or edit an audio clip"
        )
        self.sound_browse_button.setText("Folder" if is_pack else "Add")
        self.sound_browse_button.setToolTip(
            "Copy every supported sound from a folder into this pad's managed sound pack."
            if is_pack
            else "Choose an audio file and preview it before adding it."
        )
        self.sound_edit_button.setEnabled(not is_pack)
        self.sound_edit_button.setVisible(not is_pack)
        if is_pack:
            count = len(sound_pack_files(self._editor_pack_path))
            summary = f"{count} sound{'s' if count != 1 else ''}, random" if count else "No folder selected"
            self.sound_summary_label.setText(summary)
            self.sound_summary_label.setToolTip(
                f"Randomly plays one of {count} sounds on each trigger."
                if count
                else "Choose a folder containing sound clips."
            )
        else:
            self.sound_summary_label.clear()
            self.sound_summary_label.setToolTip("")
        self.sound_summary_label.setVisible(is_pack)

    def log(self, message: str) -> None:
        self.log_box.appendPlainText(message)

    def _save_config_safely(self, setting_name: str) -> bool:
        try:
            self.config_manager.save(self.config)
            return True
        except Exception as exc:
            self.log(f"Could not save {setting_name}: {exc}")
            return False

    @staticmethod
    def _qr_pixmap(url: str) -> QPixmap:
        image = qrcode.make(url).convert("RGBA")
        data = image.tobytes("raw", "RGBA")
        qimage = QImage(data, image.width, image.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimage).scaled(
            QR_DISPLAY_SIZE,
            QR_DISPLAY_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    @staticmethod
    def _normalize_hex(value: str) -> str:
        trimmed = value.strip()
        if trimmed.startswith("#") and len(trimmed) == 7:
            color = QColor(trimmed)
            if color.isValid():
                return trimmed.upper()
        return "#3A4A63"

    @staticmethod
    def _coerce_bool(value: object, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return True
            if normalized in {"false", "no", "off", "0"}:
                return False
        return fallback

    @staticmethod
    def _clamp_number(value: object, minimum, maximum, fallback, converter):
        try:
            converted = converter(value)
        except (TypeError, ValueError, OverflowError):
            converted = fallback
        if isinstance(converted, float) and not math.isfinite(converted):
            converted = fallback
        return max(minimum, min(maximum, converted))

    def _mic_gate_label_text(self) -> str:
        threshold = int(round(self.mic_gate_slider.value()))
        return f"Gate threshold: {threshold} dB"

    def _playback_volume_label_text(self) -> str:
        return f"{max(0, min(200, int(self.playback_volume_slider.value())))}%"

    def _selected_pad_volume_percent(self) -> int:
        return max(0, min(200, int(self.volume_slider.value())))

    @staticmethod
    def _normalize_remote_playback_mode(value: object) -> str:
        return "replace" if str(value).strip().lower() == "replace" else "layer"

    @staticmethod
    def _normalize_remote_connection_mode(value: object) -> str:
        return "internet" if str(value).strip().lower() == "internet" else "local"

    @staticmethod
    def _normalize_sound_mode(value: object) -> str:
        return "pack" if str(value).strip().lower() == "pack" else "single"

    @staticmethod
    def _select_combo_device(combo: QComboBox, device_id: str, device_name: str) -> bool:
        index = combo.findText(device_name, Qt.MatchFlag.MatchFixedString) if device_name else -1
        if index < 0:
            index = combo.findData(device_id)
        if index >= 0:
            combo.setCurrentIndex(index)
            return True
        return False
