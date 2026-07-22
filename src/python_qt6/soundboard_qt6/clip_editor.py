from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import tempfile
from typing import Callable
import uuid

import numpy as np
import soundfile as sf
from PySide6.QtCore import QLocale, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .sound_validation import MAX_SOUND_DURATION_SECONDS


@dataclass(frozen=True, slots=True)
class AudioClipMetadata:
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int
    frames: int


class SoundClipError(ValueError):
    pass


def read_clip_metadata(path_text: str | Path) -> AudioClipMetadata:
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))

    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise SoundClipError(f"This audio format could not be opened: {path.name}") from exc

    sample_rate = int(info.samplerate or 0)
    frames = int(info.frames or 0)
    channels = int(info.channels or 0)
    duration = frames / sample_rate if frames > 0 and sample_rate > 0 else float(info.duration or 0.0)
    if sample_rate <= 0 or frames <= 0 or channels <= 0 or duration <= 0:
        raise SoundClipError(f"This audio file is empty or unreadable: {path.name}")

    return AudioClipMetadata(
        path=path.resolve(),
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
    )


def build_waveform_envelope(metadata: AudioClipMetadata, *, point_count: int = 720) -> np.ndarray:
    point_count = max(64, int(point_count))
    envelope = np.zeros(point_count, dtype=np.float32)
    frames_per_point = max(1.0, metadata.frames / point_count)
    sample_window = max(64, min(4096, int(math.ceil(frames_per_point))))

    try:
        with sf.SoundFile(str(metadata.path), mode="r") as source:
            for index in range(point_count):
                center = int((index + 0.5) * frames_per_point)
                start = max(0, min(metadata.frames - 1, center - sample_window // 2))
                source.seek(start)
                samples = source.read(sample_window, dtype="float32", always_2d=True)
                if samples.size:
                    finite_samples = np.nan_to_num(
                        samples,
                        copy=False,
                        nan=0.0,
                        posinf=1.0,
                        neginf=-1.0,
                    )
                    envelope[index] = float(np.max(np.abs(finite_samples)))
    except Exception as exc:
        raise SoundClipError(f"Could not create a preview for {metadata.path.name}") from exc

    peak = float(np.max(envelope)) if envelope.size else 0.0
    if peak > 0:
        envelope /= peak
    return envelope


def export_audio_clip(
    source_path: str | Path,
    target_path: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    max_seconds: float = MAX_SOUND_DURATION_SECONDS,
) -> Path:
    metadata = read_clip_metadata(source_path)
    start = max(0.0, float(start_seconds))
    end = min(metadata.duration_seconds, float(end_seconds))
    duration = end - start
    if duration <= 0:
        raise SoundClipError("Choose a clip with a duration greater than zero.")
    if duration > max_seconds + 1e-6:
        raise SoundClipError(f"Clips must be {max_seconds:g} seconds or shorter.")

    start_frame = min(metadata.frames - 1, int(round(start * metadata.sample_rate)))
    end_frame = min(metadata.frames, int(round(end * metadata.sample_rate)))
    frame_count = max(1, end_frame - start_frame)

    try:
        with sf.SoundFile(str(metadata.path), mode="r") as source:
            source.seek(start_frame)
            samples = source.read(frame_count, dtype="float32", always_2d=True)
        if not samples.size:
            raise SoundClipError("The selected clip contains no audio.")
        samples = np.nan_to_num(
            samples,
            copy=False,
            nan=0.0,
            posinf=1.0,
            neginf=-1.0,
        )

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(
            str(target),
            samples,
            metadata.sample_rate,
            format="WAV",
            subtype="PCM_24",
        )
        return target.resolve()
    except SoundClipError:
        raise
    except Exception as exc:
        raise SoundClipError(f"Could not create the edited clip: {exc}") from exc


class AudioDropZone(QFrame):
    file_dropped = Signal(str)
    browse_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AudioDropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        badge = QLabel("AUDIO")
        badge.setObjectName("DropZoneBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(58, 44)

        copy_layout = QVBoxLayout()
        copy_layout.setSpacing(3)
        title = QLabel("Drop an audio file here")
        title.setObjectName("DropZoneTitle")
        self.detail_label = QLabel("or click to choose WAV, MP3, OGG, or FLAC")
        self.detail_label.setObjectName("DropZoneDetail")
        self.detail_label.setWordWrap(True)
        copy_layout.addWidget(title)
        copy_layout.addWidget(self.detail_label)

        layout.addWidget(badge)
        layout.addLayout(copy_layout, 1)

    def set_file(self, path: Path | None) -> None:
        self.detail_label.setText(path.name if path is not None else "or click to choose WAV, MP3, OGG, or FLAC")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._clear_drag_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._clear_drag_state()
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.file_dropped.emit(url.toLocalFile())
                event.acceptProposedAction()
                return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.browse_requested.emit()
        super().mouseReleaseEvent(event)

    def _clear_drag_state(self) -> None:
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)


class WaveformTrimWidget(QWidget):
    selection_changed = Signal(float, float)

    def __init__(self, max_seconds: float) -> None:
        super().__init__()
        self._max_seconds = max_seconds
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead_seconds: float | None = None
        self._envelope = np.zeros(0, dtype=np.float32)
        self._drag_mode = ""
        self._drag_offset = 0.0
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_audio(self, envelope: np.ndarray, duration: float, start: float, end: float) -> None:
        self._envelope = np.asarray(envelope, dtype=np.float32)
        self._duration = max(0.0, float(duration))
        self.set_selection(start, end, emit=False)

    def set_selection(self, start: float, end: float, *, emit: bool = False) -> None:
        if self._duration <= 0:
            self._start = 0.0
            self._end = 0.0
        else:
            start = max(0.0, min(float(start), self._duration))
            end = max(start, min(float(end), self._duration))
            if end - start > self._max_seconds:
                end = start + self._max_seconds
            self._start = start
            self._end = end
        self.update()
        if emit:
            self.selection_changed.emit(self._start, self._end)

    def set_playhead(self, seconds: float | None) -> None:
        if seconds is None or self._duration <= 0:
            self._playhead_seconds = None
        else:
            self._playhead_seconds = max(0.0, min(self._duration, float(seconds)))
        self.update()

    def playhead_seconds(self) -> float | None:
        return self._playhead_seconds

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -22)
        background = QPainterPath()
        background.addRoundedRect(rect, 13, 13)
        painter.fillPath(background, QColor("#0B1220"))
        painter.setPen(QPen(QColor("#2B3952"), 1))
        painter.drawPath(background)

        if self._duration <= 0 or not self._envelope.size:
            painter.setPen(QColor("#8190A7"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Choose an audio file to begin")
            painter.end()
            return

        center_y = rect.center().y()
        usable_height = rect.height() * 0.39
        count = len(self._envelope)
        painter.setPen(QPen(QColor(72, 103, 135), 1))
        for index, amplitude in enumerate(self._envelope):
            x = rect.left() + (index / max(1, count - 1)) * rect.width()
            height = max(1.0, float(amplitude) * usable_height)
            painter.drawLine(QPointF(x, center_y - height), QPointF(x, center_y + height))

        start_x = self._seconds_to_x(self._start, rect)
        end_x = self._seconds_to_x(self._end, rect)
        selected_rect = QRectF(start_x, rect.top(), max(1.0, end_x - start_x), rect.height())
        painter.save()
        painter.setClipRect(selected_rect)
        painter.setPen(QPen(QColor("#55D8FF"), 1.4))
        for index, amplitude in enumerate(self._envelope):
            x = rect.left() + (index / max(1, count - 1)) * rect.width()
            height = max(1.0, float(amplitude) * usable_height)
            painter.drawLine(QPointF(x, center_y - height), QPointF(x, center_y + height))
        painter.restore()

        painter.fillRect(QRectF(rect.left(), rect.top(), max(0.0, start_x - rect.left()), rect.height()), QColor(3, 7, 14, 142))
        painter.fillRect(QRectF(end_x, rect.top(), max(0.0, rect.right() - end_x), rect.height()), QColor(3, 7, 14, 142))
        painter.setPen(QPen(QColor("#6FF5A8"), 2.2))
        painter.drawLine(QPointF(start_x, rect.top() + 3), QPointF(start_x, rect.bottom() - 3))
        painter.drawLine(QPointF(end_x, rect.top() + 3), QPointF(end_x, rect.bottom() - 3))
        painter.setPen(QPen(QColor("#6FF5A8"), 1))
        painter.setBrush(QColor("#6FF5A8"))
        painter.drawRoundedRect(QRectF(start_x - 4, rect.top() + 8, 8, 28), 4, 4)
        painter.drawRoundedRect(QRectF(end_x - 4, rect.top() + 8, 8, 28), 4, 4)

        painter.setPen(QColor("#8190A7"))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 5, 90, 17), Qt.AlignmentFlag.AlignLeft, "0:00")
        painter.drawText(
            QRectF(rect.right() - 110, rect.bottom() + 5, 110, 17),
            Qt.AlignmentFlag.AlignRight,
            _format_clock(self._duration),
        )

        if self._playhead_seconds is not None:
            playhead_x = self._seconds_to_x(self._playhead_seconds, rect)
            painter.setPen(QPen(QColor(8, 12, 19, 155), 4.5))
            painter.drawLine(QPointF(playhead_x, rect.top() + 2), QPointF(playhead_x, rect.bottom() - 2))
            painter.setPen(QPen(QColor("#FFB65C"), 2.2))
            painter.drawLine(QPointF(playhead_x, rect.top() + 2), QPointF(playhead_x, rect.bottom() - 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFB65C"))
            painter.drawEllipse(QPointF(playhead_x, rect.top() + 8), 5.5, 5.5)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -22)
        clicked_seconds = self._x_to_seconds(event.position().x(), rect)
        start_x = self._seconds_to_x(self._start, rect)
        end_x = self._seconds_to_x(self._end, rect)
        if abs(event.position().x() - start_x) <= 13:
            self._drag_mode = "start"
        elif abs(event.position().x() - end_x) <= 13:
            self._drag_mode = "end"
        elif self._start <= clicked_seconds <= self._end:
            self._drag_mode = "move"
            self._drag_offset = clicked_seconds - self._start
        else:
            width = min(self._max_seconds, max(0.1, self._end - self._start))
            start = max(0.0, min(self._duration - width, clicked_seconds - width / 2))
            self.set_selection(start, start + width, emit=True)
            self._drag_mode = "move"
            self._drag_offset = width / 2
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if not self._drag_mode or self._duration <= 0:
            return
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -22)
        seconds = self._x_to_seconds(event.position().x(), rect)
        minimum_duration = min(0.1, self._duration)
        if self._drag_mode == "start":
            start = max(0.0, self._end - self._max_seconds, min(seconds, self._end - minimum_duration))
            self.set_selection(start, self._end, emit=True)
        elif self._drag_mode == "end":
            end = min(self._duration, self._start + self._max_seconds, max(seconds, self._start + minimum_duration))
            self.set_selection(self._start, end, emit=True)
        else:
            width = self._end - self._start
            start = max(0.0, min(self._duration - width, seconds - self._drag_offset))
            self.set_selection(start, start + width, emit=True)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_mode = ""
        super().mouseReleaseEvent(event)

    def _seconds_to_x(self, seconds: float, rect: QRectF) -> float:
        return rect.left() + (seconds / self._duration) * rect.width() if self._duration > 0 else rect.left()

    def _x_to_seconds(self, x: float, rect: QRectF) -> float:
        ratio = (x - rect.left()) / max(1.0, rect.width())
        return max(0.0, min(self._duration, ratio * self._duration))


class SoundClipEditorDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        initial_path: str = "",
        *,
        preview_callback: Callable[[str], None] | None = None,
        stop_preview_callback: Callable[[], None] | None = None,
        preview_position_callback: Callable[[], float | None] | None = None,
        max_seconds: float = MAX_SOUND_DURATION_SECONDS,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Edit sound clip")
        self.setMinimumSize(680, 570)
        self.resize(760, 620)
        self._max_seconds = max_seconds
        self._preview_callback = preview_callback
        self._stop_preview_callback = stop_preview_callback
        self._preview_position_callback = preview_position_callback
        self._preview_selection_start = 0.0
        self._preview_selection_end = 0.0
        self._metadata: AudioClipMetadata | None = None
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="soundboard-clip-")
        self.result_path = ""
        self._updating_times = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(30)
        self._preview_timer.timeout.connect(self._update_preview_playhead)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("ClipEditorCard")
        outer_layout.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_copy = QVBoxLayout()
        title_copy.setSpacing(2)
        title = QLabel("Edit sound clip")
        title.setObjectName("ClipEditorTitle")
        subtitle = QLabel("Choose the part you want to use on this pad.")
        subtitle.setObjectName("ClipEditorSubtitle")
        title_copy.addWidget(title)
        title_copy.addWidget(subtitle)
        limit_badge = QLabel(f"{max_seconds:g} SEC MAX")
        limit_badge.setObjectName("ClipLimitBadge")
        limit_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close_button = QPushButton("Close")
        close_button.setObjectName("ClipCloseButton")
        close_button.clicked.connect(self.reject)
        title_row.addLayout(title_copy, 1)
        title_row.addWidget(limit_badge)
        title_row.addWidget(close_button)
        layout.addLayout(title_row)

        self.drop_zone = AudioDropZone()
        self.drop_zone.file_dropped.connect(self.load_file)
        self.drop_zone.browse_requested.connect(self.browse_file)
        layout.addWidget(self.drop_zone)

        self.status_label = QLabel("Add an audio file to see its waveform.")
        self.status_label.setObjectName("ClipStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.waveform = WaveformTrimWidget(max_seconds)
        self.waveform.selection_changed.connect(self._waveform_selection_changed)
        layout.addWidget(self.waveform, 1)

        timing_row = QHBoxLayout()
        timing_row.setSpacing(10)
        start_label = QLabel("Start")
        self.start_spin = self._create_time_spin()
        end_label = QLabel("End")
        self.end_spin = self._create_time_spin()
        self.selection_label = QLabel("0.0 sec selected")
        self.selection_label.setObjectName("ClipSelectionLabel")
        self.selection_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        timing_row.addWidget(start_label)
        timing_row.addWidget(self.start_spin)
        timing_row.addWidget(end_label)
        timing_row.addWidget(self.end_spin)
        timing_row.addWidget(self.selection_label, 1)
        layout.addLayout(timing_row)

        button_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview clip")
        self.preview_button.setObjectName("ClipSecondaryButton")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("ClipSecondaryButton")
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("ClipSecondaryButton")
        self.use_button = QPushButton("Use clip")
        self.use_button.setObjectName("ClipPrimaryButton")
        self.preview_button.clicked.connect(self.preview_clip)
        self.stop_button.clicked.connect(self.stop_preview)
        cancel_button.clicked.connect(self.reject)
        self.use_button.clicked.connect(self.use_clip)
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(self.use_button)
        layout.addLayout(button_row)

        self.start_spin.valueChanged.connect(self._start_changed)
        self.end_spin.valueChanged.connect(self._end_changed)
        self._set_controls_enabled(False)
        self._apply_styles()

        if initial_path:
            self.load_file(initial_path)

    def _create_time_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setLocale(QLocale.c())
        spin.setObjectName("ClipTimeSpin")
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix(" s")
        spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(108)
        return spin

    def browse_file(self) -> None:
        start_dir = str(self._metadata.path.parent) if self._metadata is not None else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Sound File",
            start_dir,
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*.*)",
        )
        if path:
            self.load_file(path)

    def load_file(self, path_text: str) -> None:
        self.stop_preview()
        try:
            metadata = read_clip_metadata(path_text)
            envelope = build_waveform_envelope(metadata)
        except (FileNotFoundError, SoundClipError) as exc:
            self._clear_loaded_file()
            self.status_label.setProperty("error", True)
            self.status_label.setText(str(exc))
            self._refresh_status_style()
            return

        self._metadata = metadata
        self.drop_zone.set_file(metadata.path)
        selection_end = min(metadata.duration_seconds, self._max_seconds)
        self.start_spin.setRange(0.0, metadata.duration_seconds)
        self.end_spin.setRange(0.0, metadata.duration_seconds)
        self.waveform.set_audio(envelope, metadata.duration_seconds, 0.0, selection_end)
        self._set_time_values(0.0, selection_end)
        self._set_controls_enabled(True)
        self.status_label.setProperty("error", False)
        if metadata.duration_seconds > self._max_seconds:
            self.status_label.setText(
                f"{metadata.path.name} is {_format_duration(metadata.duration_seconds)}. "
                f"Move the selection to choose up to {self._max_seconds:g} seconds."
            )
        else:
            self.status_label.setText(
                f"{metadata.path.name}  |  {_format_duration(metadata.duration_seconds)}  |  "
                f"{metadata.sample_rate / 1000:g} kHz  |  {metadata.channels} ch"
            )
        self._refresh_status_style()

    def _clear_loaded_file(self) -> None:
        self._metadata = None
        self.drop_zone.set_file(None)
        self.waveform.set_audio(np.zeros(0, dtype=np.float32), 0.0, 0.0, 0.0)
        self.start_spin.setRange(0.0, 0.0)
        self.end_spin.setRange(0.0, 0.0)
        self._set_time_values(0.0, 0.0)
        self._set_controls_enabled(False)

    def preview_clip(self) -> None:
        if self._metadata is None or self._preview_callback is None:
            return
        self.stop_preview()
        try:
            preview_path = self._selected_audio_path(f"preview-{uuid.uuid4().hex}.wav")
            self._preview_selection_start = self.start_spin.value()
            self._preview_selection_end = self.end_spin.value()
            self.waveform.set_playhead(self._preview_selection_start)
            self._preview_callback(str(preview_path))
            self.preview_button.setText("Playing")
            self._preview_timer.start()
        except SoundClipError as exc:
            self._show_error(str(exc))

    def stop_preview(self) -> None:
        self._stop_preview_visual()
        if self._stop_preview_callback is not None:
            self._stop_preview_callback()

    def _update_preview_playhead(self) -> None:
        if self._preview_position_callback is None:
            self._stop_preview_visual()
            return
        position = self._preview_position_callback()
        if position is None:
            self._stop_preview_visual()
            return
        absolute_position = min(
            self._preview_selection_end,
            self._preview_selection_start + max(0.0, position),
        )
        self.waveform.set_playhead(absolute_position)

    def _stop_preview_visual(self) -> None:
        self._preview_timer.stop()
        self.waveform.set_playhead(None)
        self.preview_button.setText("Preview clip")

    def use_clip(self) -> None:
        if self._metadata is None:
            return
        self.stop_preview()
        try:
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", self._metadata.path.stem).strip("._-") or "sound"
            self.result_path = str(self._selected_audio_path(f"{safe_stem[:64]}-clip.wav"))
            self.accept()
        except SoundClipError as exc:
            self._show_error(str(exc))

    def cleanup(self) -> None:
        self.stop_preview()
        self._temporary_directory.cleanup()

    def done(self, result: int) -> None:  # type: ignore[override]
        self.stop_preview()
        super().done(result)

    def _selected_audio_path(self, filename: str) -> Path:
        if self._metadata is None:
            raise SoundClipError("Choose an audio file first.")
        start = self.start_spin.value()
        end = self.end_spin.value()
        tolerance = 1.0 / self._metadata.sample_rate
        if start <= tolerance and abs(end - self._metadata.duration_seconds) <= tolerance:
            return self._metadata.path
        target = Path(self._temporary_directory.name) / filename
        return export_audio_clip(
            self._metadata.path,
            target,
            start,
            end,
            max_seconds=self._max_seconds,
        )

    def _start_changed(self, value: float) -> None:
        if self._updating_times or self._metadata is None:
            return
        minimum_duration = min(0.1, self._metadata.duration_seconds)
        end = self.end_spin.value()
        start = min(value, end - minimum_duration)
        start = max(0.0, end - self._max_seconds, start)
        self._set_time_values(start, end)
        self.waveform.set_selection(start, end)

    def _end_changed(self, value: float) -> None:
        if self._updating_times or self._metadata is None:
            return
        minimum_duration = min(0.1, self._metadata.duration_seconds)
        start = self.start_spin.value()
        end = max(start + minimum_duration, value)
        end = min(self._metadata.duration_seconds, start + self._max_seconds, end)
        self._set_time_values(start, end)
        self.waveform.set_selection(start, end)

    def _waveform_selection_changed(self, start: float, end: float) -> None:
        self._set_time_values(start, end)

    def _set_time_values(self, start: float, end: float) -> None:
        self._updating_times = True
        self.start_spin.setValue(start)
        self.end_spin.setValue(end)
        self._updating_times = False
        self.selection_label.setText(f"{max(0.0, end - start):.1f} sec selected")

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.waveform, self.start_spin, self.end_spin, self.preview_button, self.stop_button, self.use_button):
            widget.setEnabled(enabled)

    def _show_error(self, message: str) -> None:
        self.status_label.setProperty("error", True)
        self.status_label.setText(message)
        self._refresh_status_style()

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QFrame#ClipEditorCard {
                background: #161F2F;
                border: 1px solid #35445E;
                border-radius: 20px;
            }
            QLabel {
                color: #D8E2F0;
                background: transparent;
                border: none;
                font-size: 13px;
            }
            QLabel#ClipEditorTitle {
                color: #F7FAFF;
                font-size: 21px;
                font-weight: 800;
                padding: 1px 0 2px 0;
            }
            QLabel#ClipEditorSubtitle, QLabel#DropZoneDetail {
                color: #91A2B9;
            }
            QLabel#ClipLimitBadge {
                color: #7BF7AF;
                background: #10271F;
                border: 1px solid #285844;
                border-radius: 10px;
                font-size: 11px;
                font-weight: 800;
                padding: 7px 10px;
            }
            QFrame#AudioDropZone {
                background: #101827;
                border: 1px dashed #405271;
                border-radius: 14px;
            }
            QFrame#AudioDropZone:hover, QFrame#AudioDropZone[dragActive="true"] {
                background: #13243A;
                border: 1px solid #4CCEF5;
            }
            QLabel#DropZoneBadge {
                color: #09202B;
                background: #55D8FF;
                border-radius: 11px;
                font-size: 11px;
                font-weight: 900;
            }
            QLabel#DropZoneTitle {
                color: #F1F6FC;
                font-size: 14px;
                font-weight: 700;
                padding: 1px 0;
            }
            QLabel#ClipStatus {
                color: #9FB0C5;
                background: #111A29;
                border-radius: 9px;
                padding: 8px 10px;
            }
            QLabel#ClipStatus[error="true"] {
                color: #FFD09A;
                background: #2A2019;
                border: 1px solid #60452B;
            }
            QLabel#ClipSelectionLabel {
                color: #7BF7AF;
                font-weight: 700;
            }
            QDoubleSpinBox#ClipTimeSpin {
                color: #F4F7FB;
                background: #0D1522;
                border: 1px solid #33435E;
                border-radius: 9px;
                padding: 7px 9px;
                selection-background-color: #2B8DD8;
            }
            QDoubleSpinBox#ClipTimeSpin:focus {
                border-color: #55D8FF;
            }
            QPushButton {
                min-height: 37px;
                padding: 0 15px;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#ClipSecondaryButton, QPushButton#ClipCloseButton {
                color: #E2EAF5;
                background: #22304A;
                border: 1px solid #324460;
            }
            QPushButton#ClipSecondaryButton:hover, QPushButton#ClipCloseButton:hover {
                background: #2B3C59;
                border-color: #4C6386;
            }
            QPushButton#ClipCloseButton {
                min-width: 58px;
                padding: 0 11px;
            }
            QPushButton#ClipPrimaryButton {
                color: #07131B;
                background: #6FF5A8;
                border: 1px solid #8DFFC0;
                min-width: 96px;
            }
            QPushButton#ClipPrimaryButton:hover {
                background: #8DFFC0;
            }
            QPushButton:disabled {
                color: #627086;
                background: #1B2638;
                border-color: #28354A;
            }
            """
        )


def _format_duration(seconds: float) -> str:
    rounded_tenths = max(0, int(round(seconds * 10)))
    minutes, tenths_in_minute = divmod(rounded_tenths, 600)
    whole_seconds, tenths = divmod(tenths_in_minute, 10)
    return f"{minutes}:{whole_seconds:02d}.{tenths}"


def _format_clock(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"
