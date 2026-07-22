from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import random
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf
try:
    import soxr
except ImportError:  # pragma: no cover - packaged app includes soxr, fallback keeps dev usable.
    soxr = None
from PySide6.QtCore import QObject, Signal

from .models import PadConfig
from .sound_validation import (
    SoundValidationError,
    sound_pack_files,
    validate_sound_duration,
    validate_sound_frames,
)


@dataclass(slots=True)
class ActiveVoice:
    pad_id: str
    label: str
    monitor_samples: np.ndarray
    virtual_samples: np.ndarray
    fallback_volume_gain: float
    remote_visible: bool
    position: int = 0


@dataclass(slots=True)
class CachedSound:
    mtime_ns: int
    size: int
    monitor_samples: np.ndarray
    virtual_samples: np.ndarray
    raw_virtual_samples: np.ndarray


@dataclass(slots=True)
class PreparedSound:
    monitor_samples: np.ndarray
    virtual_samples: np.ndarray
    raw_virtual_samples: np.ndarray


@dataclass(slots=True)
class DeviceCandidate:
    index: int
    name: str
    hostapi_name: str
    max_channels: int

    @property
    def display_name(self) -> str:
        return self.name.strip()


class QtAudioEngine(QObject):
    log = Signal(str)
    status_changed = Signal(str)
    playback_state_changed = Signal(list)
    readiness_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._sample_rate = 48_000
        self._output_channels = 2
        self._blocksize = 256
        self._input_device_id: str = ""
        self._output_device_id: str = ""
        self._duplex_stream: sd.Stream | None = None
        self._input_stream: sd.InputStream | None = None
        self._monitor_output_stream: sd.OutputStream | None = None
        self._virtual_output_stream: sd.OutputStream | None = None
        self._virtual_output_device_label = ""
        self._virtual_output_channels = self._output_channels
        self._compatibility_blocksize = 512
        self._monitor_chunks: deque[np.ndarray] = deque()
        self._monitor_buffer_frames = 0
        self._monitor_prefill_frames = self._compatibility_blocksize * 4
        self._monitor_max_buffer_frames = self._sample_rate // 2
        self._virtual_chunks: deque[np.ndarray] = deque()
        self._virtual_buffer_frames = 0
        self._virtual_output_blocksize = 256
        self._virtual_prefill_frames = self._virtual_output_blocksize * 2
        self._virtual_output_latency_seconds = 0.02
        self._virtual_fallback_blocksize = 512
        self._virtual_fallback_prefill_frames = self._virtual_fallback_blocksize * 4
        self._virtual_fallback_latency_seconds = 0.06
        self._virtual_max_buffer_frames = self._sample_rate // 2
        self._voices: list[ActiveVoice] = []
        self._pad_volume_gains: dict[str, float] = {}
        self._stream_lock = threading.RLock()
        self._voices_lock = threading.RLock()
        self._monitor_buffer_lock = threading.RLock()
        self._virtual_buffer_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._status_report_lock = threading.RLock()
        self._sound_cache: dict[str, CachedSound] = {}
        self._last_stream_status: dict[str, tuple[str, float]] = {}
        self._status_repeat_interval_seconds = 5.0
        self._last_pack_sound_paths: dict[str, str] = {}
        self._max_cached_sounds = 24
        self._max_active_voices = 32
        self._preload_thread: threading.Thread | None = None
        self._mic_mix_gain = 1.0
        self._soundboard_gain = 1.0
        self._monitor_muted = False
        self._playback_volume_gain = 1.0
        self._raw_hd_soundboard_enabled = False
        self._virtual_mic_gain = 1.0
        self._virtual_soundboard_gain = 1.0
        self._virtual_peak_ceiling = 0.92
        self._virtual_limiter_gain = 1.0
        self._limiter_release_coeff = float(np.exp(-1.0 / (self._sample_rate * 0.080)))
        self._prepared_peak_ceiling = 0.92
        self._soundboard_rms_ceiling = 0.14
        self._voice_codec_highpass_hz = 90.0
        self._voice_codec_lowpass_hz = 11_000.0
        self._mic_noise_gate_enabled = True
        self._mic_noise_gate_threshold_db = -42.0
        self._mic_gate_level = 0.0
        self._mic_gate_floor = 0.02
        self._mic_gate_hold_frames = int(self._sample_rate * 0.12)
        self._mic_gate_hold_remaining = 0
        self._audio_ready = False

    def available_input_devices(self) -> list[tuple[str, str]]:
        return self._visible_devices(is_input=True)

    def available_output_devices(self) -> list[tuple[str, str]]:
        return self._visible_devices(is_input=False)

    def rescan_devices(self) -> None:
        """Reinitialize PortAudio so Windows hot-plug changes become visible."""
        self.stop_all_sounds()
        with self._stream_lock:
            self._stop_stream_locked()
            sd._terminate()
            try:
                sd._initialize()
            except Exception:
                # Keep PortAudio initialization balanced if the first retry is transient.
                sd._initialize()

    def virtual_cable_status(self) -> str:
        try:
            output_index = self._find_virtual_cable_output_index(excluded_output_index=-1)
            if output_index is None:
                return "Virtual mic route: signed cable not installed"
            output_info = sd.query_devices(output_index)
            return (
                "Virtual mic route ready: "
                f"{output_info['name']} receives clean mic + soundboard; select CABLE Output in voice apps"
            )
        except Exception as exc:
            return f"Virtual mic route could not be checked: {exc}"

    def virtual_cable_available(self) -> bool:
        try:
            return self._find_virtual_cable_output_index(excluded_output_index=-1) is not None
        except Exception:
            return False

    def audio_ready(self) -> bool:
        with self._stream_lock:
            return self._audio_ready

    def active_remote_pad_ids(self) -> list[str]:
        with self._voices_lock:
            return self._active_remote_pad_ids_locked()

    def set_input_device(self, device_id: str) -> None:
        self.set_devices(device_id, self._output_device_id)

    def set_output_device(self, device_id: str) -> None:
        self.set_devices(self._input_device_id, device_id)

    def set_devices(self, input_device_id: str, output_device_id: str) -> None:
        self._input_device_id = input_device_id
        self._output_device_id = output_device_id
        self.restart_stream()

    def set_monitor_muted(self, muted: bool) -> None:
        self._monitor_muted = muted

    def set_playback_volume_percent(self, volume_percent: int) -> None:
        clamped = max(0, min(200, int(volume_percent)))
        self._playback_volume_gain = clamped / 100.0

    def set_pad_volume_percent(self, pad_id: str, volume_percent: int) -> None:
        with self._voices_lock:
            self._pad_volume_gains[pad_id] = self._volume_gain(volume_percent)

    def set_pad_volumes(self, pads: list[PadConfig]) -> None:
        with self._voices_lock:
            self._pad_volume_gains = {pad.id: self._volume_gain(pad.volume_percent) for pad in pads}

    def set_mic_noise_gate(self, enabled: bool, threshold_db: float) -> None:
        self._mic_noise_gate_enabled = enabled
        self._mic_noise_gate_threshold_db = float(max(-70.0, min(-20.0, threshold_db)))

    def set_raw_hd_soundboard(self, enabled: bool) -> None:
        self._raw_hd_soundboard_enabled = enabled

    def restart_stream(self) -> None:
        self.stop_all_sounds()
        with self._stream_lock:
            self._stop_stream_locked()

            input_index = self._resolve_index(self._input_device_id)
            output_index = self._resolve_index(self._output_device_id)
            if input_index is None or output_index is None:
                self.status_changed.emit(
                    "Mixer idle: choose both microphone input and monitor output"
                )
                return

            try:
                input_info = sd.query_devices(input_index)
                output_info = sd.query_devices(output_index)
                input_channels = self._supported_channel_count(
                    input_index,
                    int(input_info["max_input_channels"]),
                    is_input=True,
                )
                output_channels = self._supported_channel_count(
                    output_index,
                    int(output_info["max_output_channels"]),
                    is_input=False,
                )
                if input_channels is None:
                    raise RuntimeError(f"{input_info['name']} does not support 48 kHz microphone input.")
                if output_channels is None:
                    raise RuntimeError(f"{output_info['name']} does not support 48 kHz playback.")
                self._virtual_limiter_gain = 1.0
                mixer_mode = self._start_mixer_streams_locked(
                    input_index=input_index,
                    output_index=output_index,
                    input_channels=input_channels,
                    output_channels=output_channels,
                )
                virtual_status = self._start_virtual_output_locked(excluded_output_index=output_index)
                self._update_audio_ready_locked()
                self.status_changed.emit(
                    f"Mixer running ({mixer_mode}): {input_info['name']} -> {output_info['name']} | "
                    f"{virtual_status}"
                )
            except Exception as exc:
                self._stop_stream_locked()
                self.status_changed.emit(f"Mixer unavailable: {exc}")

    def trigger_pad(
        self,
        pad: PadConfig,
        *,
        route_to_virtual: bool = True,
        log_action: bool = True,
    ) -> None:
        candidates = self._pad_sound_paths(pad)
        if not candidates:
            if pad.sound_mode == "pack":
                detail = "no sound pack assigned" if not pad.sound_pack_path.strip() else "sound pack is empty or missing"
                self.log.emit(f"{pad.label}: {detail}.")
            elif pad.sound_path.strip():
                self.log.emit(f"{pad.label}: sound file not found -> {Path(pad.sound_path).expanduser()}")
            else:
                self.log.emit(f"{pad.label}: no sound source assigned.")
            return

        if pad.sound_mode == "pack":
            random.shuffle(candidates)
            last_path = self._last_pack_sound_paths.get(pad.id)
            if len(candidates) > 1 and last_path:
                candidates.sort(key=lambda path: str(path.resolve()) == last_path)

        sound_path: Path | None = None
        prepared: PreparedSound | None = None
        for candidate in candidates:
            prepared = self._get_prepared_sound(candidate, pad.label, emit_errors=True)
            if prepared is not None:
                sound_path = candidate
                break
        if prepared is None or sound_path is None:
            return
        if pad.sound_mode == "pack":
            self._last_pack_sound_paths[pad.id] = str(sound_path.resolve())

        if prepared.monitor_samples.size == 0:
            self.log.emit(f"{pad.label}: file is empty.")
            return

        volume = self._volume_gain(pad.volume_percent)
        virtual_samples = prepared.raw_virtual_samples if self._raw_hd_soundboard_enabled else prepared.virtual_samples
        if not route_to_virtual:
            virtual_samples = np.zeros_like(virtual_samples, dtype=np.float32)

        with self._voices_lock:
            previous_remote_pad_ids = self._active_remote_pad_ids_locked()
            self._pad_volume_gains[pad.id] = volume
            self._voices.append(
                ActiveVoice(
                    pad_id=pad.id,
                    label=pad.label,
                    monitor_samples=prepared.monitor_samples,
                    virtual_samples=virtual_samples,
                    fallback_volume_gain=volume,
                    remote_visible=route_to_virtual,
                )
            )
            if len(self._voices) > self._max_active_voices:
                del self._voices[: len(self._voices) - self._max_active_voices]
            active_remote_pad_ids = self._active_remote_pad_ids_locked()

        if route_to_virtual or active_remote_pad_ids != previous_remote_pad_ids:
            self.playback_state_changed.emit(active_remote_pad_ids)

        if log_action:
            action = "Previewed" if not route_to_virtual else "Triggered"
            self.log.emit(f"{action} {pad.label} at {pad.volume_percent}%")

    def stop_pad_sounds(self, pad_id: str) -> None:
        with self._voices_lock:
            previous_remote_ids = self._active_remote_pad_ids_locked()
            self._voices = [voice for voice in self._voices if voice.pad_id != pad_id]
            active_remote_ids = self._active_remote_pad_ids_locked()
        if active_remote_ids != previous_remote_ids:
            self.playback_state_changed.emit(active_remote_ids)

    def pad_playback_position_seconds(self, pad_id: str) -> float | None:
        with self._voices_lock:
            for voice in reversed(self._voices):
                if voice.pad_id == pad_id:
                    return voice.position / self._sample_rate
        return None

    def stop_all_sounds(self) -> None:
        with self._voices_lock:
            self._voices.clear()
        self._reset_monitor_output_queue()
        self._reset_virtual_output_queue()
        self.playback_state_changed.emit([])

    def preload_pads(self, pads: list[PadConfig]) -> None:
        paths = []
        seen = set()
        for pad in pads:
            pad_paths = self._pad_sound_paths(pad)
            if pad.sound_mode == "pack":
                pad_paths = pad_paths[:4]
            for sound_path in pad_paths:
                key = str(sound_path)
                if key in seen:
                    continue
                seen.add(key)
                paths.append((sound_path, pad.label))
                if len(paths) >= self._max_cached_sounds:
                    break
            if len(paths) >= self._max_cached_sounds:
                break

        if not paths:
            return

        def worker() -> None:
            for sound_path, label in paths:
                self._get_prepared_sound(sound_path, label, emit_errors=False)

        self._preload_thread = threading.Thread(target=worker, name="SoundboardPreload", daemon=True)
        self._preload_thread.start()

    @staticmethod
    def _pad_sound_paths(pad: PadConfig) -> list[Path]:
        if pad.sound_mode == "pack":
            return sound_pack_files(pad.sound_pack_path)
        if not pad.sound_path.strip():
            return []
        sound_path = Path(pad.sound_path).expanduser()
        return [sound_path] if sound_path.is_file() else []

    def stop(self) -> None:
        preload_thread = self._preload_thread
        if preload_thread is not None and preload_thread.is_alive() and preload_thread is not threading.current_thread():
            preload_thread.join(timeout=2)
        with self._voices_lock:
            self._voices.clear()
        self.playback_state_changed.emit([])
        with self._stream_lock:
            self._stop_stream_locked()
        self.status_changed.emit("Mixer stopped")

    def shutdown(self) -> None:
        self.stop()

    def _start_mixer_streams_locked(
        self,
        *,
        input_index: int,
        output_index: int,
        input_channels: int,
        output_channels: int,
    ) -> str:
        """Prefer a shared duplex clock, with an input-clock fallback for wireless devices."""
        duplex_stream: sd.Stream | None = None
        try:
            duplex_stream = sd.Stream(
                samplerate=self._sample_rate,
                blocksize=self._blocksize,
                dtype="float32",
                channels=(input_channels, output_channels),
                device=(input_index, output_index),
                latency=("low", "low"),
                callback=self._audio_callback,
                prime_output_buffers_using_stream_callback=True,
            )
            duplex_stream.start()
            self._duplex_stream = duplex_stream
            return "synchronized"
        except Exception:
            if duplex_stream is not None:
                try:
                    duplex_stream.close()
                except Exception:
                    pass

        self._reset_monitor_output_queue(prefill_frames=self._monitor_prefill_frames)
        self._input_stream = sd.InputStream(
            samplerate=self._sample_rate,
            blocksize=self._compatibility_blocksize,
            dtype="float32",
            channels=input_channels,
            device=input_index,
            latency="high",
            callback=self._input_callback,
        )
        self._monitor_output_stream = sd.OutputStream(
            samplerate=self._sample_rate,
            blocksize=self._compatibility_blocksize,
            dtype="float32",
            channels=output_channels,
            device=output_index,
            latency="high",
            callback=self._monitor_output_callback,
            prime_output_buffers_using_stream_callback=True,
        )
        self._input_stream.start()
        self._monitor_output_stream.start()
        return "wireless compatibility"

    def _audio_callback(self, indata, outdata, frames, time_info, status) -> None:
        del time_info
        if status:
            self._report_stream_status("Audio callback status", status)

        mic_frames = self._normalize_channels(np.asarray(indata, dtype=np.float32), self._output_channels)
        monitor_frames = self._render_mix_block(frames, mic_frames)
        outdata[:] = self._normalize_channels(monitor_frames, outdata.shape[1])

    def _input_callback(self, indata, frames, time_info, status) -> None:
        del time_info
        if status:
            self._report_stream_status("Microphone input status", status)

        mic_frames = self._normalize_channels(np.asarray(indata, dtype=np.float32), self._output_channels)
        monitor_frames = self._render_mix_block(frames, mic_frames)
        self._push_monitor_output_frames(monitor_frames)

    def _monitor_output_callback(self, outdata, frames, time_info, status) -> None:
        del time_info
        if status:
            self._report_stream_status("Playback output status", status)

        outdata[:] = self._pop_monitor_output_frames(frames, outdata.shape[1])

    def _render_mix_block(self, frames: int, mic_frames: np.ndarray) -> np.ndarray:
        monitor_sound_frames, virtual_sound_frames = self._mix_voices(frames, self._output_channels)
        virtual_frames = self._prepare_voice_app_mix(mic_frames, virtual_sound_frames)
        monitor_frames = np.clip(
            monitor_sound_frames * self._soundboard_gain * self._playback_volume_gain,
            -1.0,
            1.0,
        )
        if self._monitor_muted:
            monitor_frames.fill(0.0)
        self._push_virtual_output_frames(virtual_frames)
        return monitor_frames

    def _report_stream_status(self, source: str, status: object) -> None:
        detail = str(status)
        now = time.monotonic()
        with self._status_report_lock:
            previous = self._last_stream_status.get(source)
            if (
                previous is not None
                and previous[0] == detail
                and now - previous[1] < self._status_repeat_interval_seconds
            ):
                return
            self._last_stream_status[source] = (detail, now)
        self.log.emit(f"{source}: {detail}")

    def _mix_voices(self, frame_count: int, channel_count: int) -> tuple[np.ndarray, np.ndarray]:
        monitor_mixed = np.zeros((frame_count, channel_count), dtype=np.float32)
        virtual_mixed = np.zeros((frame_count, channel_count), dtype=np.float32)
        finished: list[ActiveVoice] = []
        active_remote_pad_ids: list[str] | None = None

        with self._voices_lock:
            for voice in self._voices:
                remaining = voice.monitor_samples.shape[0] - voice.position
                if remaining <= 0:
                    finished.append(voice)
                    continue

                take = min(frame_count, remaining)
                monitor_chunk = voice.monitor_samples[voice.position : voice.position + take, :channel_count]
                virtual_chunk = voice.virtual_samples[voice.position : voice.position + take, :channel_count]
                volume_gain = self._pad_volume_gains.get(voice.pad_id, voice.fallback_volume_gain)
                monitor_mixed[:take] += monitor_chunk * volume_gain
                virtual_mixed[:take] += virtual_chunk * volume_gain
                voice.position += take
                if voice.position >= voice.monitor_samples.shape[0]:
                    finished.append(voice)

            if finished:
                finished_ids = {id(voice) for voice in finished}
                self._voices = [voice for voice in self._voices if id(voice) not in finished_ids]
                if any(voice.remote_visible for voice in finished):
                    active_remote_pad_ids = self._active_remote_pad_ids_locked()

        if active_remote_pad_ids is not None:
            self.playback_state_changed.emit(active_remote_pad_ids)

        return monitor_mixed, virtual_mixed

    def _active_remote_pad_ids_locked(self) -> list[str]:
        return list(dict.fromkeys(voice.pad_id for voice in self._voices if voice.remote_visible))

    def _set_audio_ready(self, ready: bool) -> None:
        normalized = bool(ready)
        if self._audio_ready == normalized:
            return
        self._audio_ready = normalized
        self.readiness_changed.emit(normalized)

    def _update_audio_ready_locked(self) -> None:
        mixer_running = self._duplex_stream is not None or (
            self._input_stream is not None and self._monitor_output_stream is not None
        )
        route_available = self._virtual_output_stream is not None
        self._set_audio_ready(mixer_running and route_available)

    @staticmethod
    def _volume_gain(volume_percent: int) -> float:
        return max(0.0, min(2.0, int(volume_percent) / 100.0))

    def _prepare_samples(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        samples = self._sanitize_samples(samples)
        samples = self._normalize_channels(samples, self._output_channels)
        if sample_rate == self._sample_rate:
            return samples

        if len(samples) == 0:
            return samples

        if soxr is not None:
            return np.asarray(
                soxr.resample(samples, sample_rate, self._sample_rate, quality="HQ"),
                dtype=np.float32,
            )

        return self._linear_resample(samples, sample_rate)

    def _get_prepared_sound(self, sound_path: Path, label: str, *, emit_errors: bool) -> PreparedSound | None:
        if not sound_path.is_file():
            if emit_errors:
                self.log.emit(f"{label}: sound file not found -> {sound_path}")
            return None

        try:
            validate_sound_duration(sound_path)
        except SoundValidationError as exc:
            if emit_errors:
                self.log.emit(str(exc))
            return None

        try:
            stat = sound_path.stat()
        except OSError as exc:
            if emit_errors:
                self.log.emit(f"{label}: file stat error -> {exc}")
            return None

        cache_key = str(sound_path.resolve())
        with self._cache_lock:
            cached = self._sound_cache.get(cache_key)
            if cached and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
                self._sound_cache.pop(cache_key)
                self._sound_cache[cache_key] = cached
                return PreparedSound(
                    monitor_samples=cached.monitor_samples,
                    virtual_samples=cached.virtual_samples,
                    raw_virtual_samples=cached.raw_virtual_samples,
                )

        try:
            samples, sample_rate = sf.read(
                str(sound_path),
                dtype="float32",
                always_2d=True,
            )
            # Recheck decoded frames in case the file changed after metadata validation.
            validate_sound_frames(samples.shape[0], sample_rate, sound_path.name)
            monitor_samples = self._prepare_samples(samples, sample_rate)
            monitor_samples = self._apply_edge_fades(monitor_samples)
            monitor_samples = self._prevent_source_clipping(monitor_samples)
            virtual_samples = self._prepare_soundboard_for_voice_codec(monitor_samples)
            raw_virtual_samples = monitor_samples.astype(np.float32, copy=False)
        except Exception as exc:
            if emit_errors:
                self.log.emit(f"{label}: load error -> {exc}")
            return None

        with self._cache_lock:
            self._sound_cache[cache_key] = CachedSound(
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
                monitor_samples=monitor_samples,
                virtual_samples=virtual_samples,
                raw_virtual_samples=raw_virtual_samples,
            )
            while len(self._sound_cache) > self._max_cached_sounds:
                oldest_key = next(iter(self._sound_cache))
                self._sound_cache.pop(oldest_key, None)
        return PreparedSound(
            monitor_samples=monitor_samples,
            virtual_samples=virtual_samples,
            raw_virtual_samples=raw_virtual_samples,
        )

    def _linear_resample(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        duration = len(samples) / sample_rate
        target_length = max(1, int(duration * self._sample_rate))
        source_positions = np.linspace(0, len(samples) - 1, num=len(samples), dtype=np.float32)
        target_positions = np.linspace(0, len(samples) - 1, num=target_length, dtype=np.float32)

        resampled = np.empty((target_length, samples.shape[1]), dtype=np.float32)
        for channel_index in range(samples.shape[1]):
            resampled[:, channel_index] = np.interp(
                target_positions,
                source_positions,
                samples[:, channel_index],
            ).astype(np.float32)
        return resampled

    def _prepare_voice_app_mix(self, mic_frames: np.ndarray, sound_frames: np.ndarray) -> np.ndarray:
        # Voice apps consume microphone-style mono most reliably; avoid destructive stereo cancellation.
        mic_frames = self._sanitize_samples(mic_frames)
        sound_frames = self._sanitize_samples(sound_frames)
        mic_mono = self._downmix_to_mono(mic_frames)
        mic_mono = self._apply_mic_noise_gate(mic_mono)
        sound_mono = self._downmix_to_mono(sound_frames)
        mono = mic_mono * self._virtual_mic_gain + sound_mono * self._virtual_soundboard_gain
        mono = self._apply_virtual_limiter(mono)
        return np.repeat(mono.astype(np.float32, copy=False), self._output_channels, axis=1)

    def _prevent_source_clipping(self, samples: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak <= self._prepared_peak_ceiling:
            return samples.astype(np.float32, copy=False)
        return (samples * (self._prepared_peak_ceiling / peak)).astype(np.float32, copy=False)

    def _normalize_soundboard_loudness(self, samples: np.ndarray) -> np.ndarray:
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        if rms <= self._soundboard_rms_ceiling or rms <= 1e-9:
            return samples.astype(np.float32, copy=False)
        return (samples * (self._soundboard_rms_ceiling / rms)).astype(np.float32, copy=False)

    def _prepare_soundboard_for_voice_codec(self, samples: np.ndarray) -> np.ndarray:
        mono = self._downmix_soundboard_for_voice_codec(samples)
        mono = self._apply_voice_codec_bandpass(mono)
        mono = self._normalize_soundboard_loudness(mono)
        mono = self._prevent_source_clipping(mono)
        return np.repeat(mono.astype(np.float32, copy=False), self._output_channels, axis=1)

    def _downmix_soundboard_for_voice_codec(self, samples: np.ndarray) -> np.ndarray:
        if samples.ndim == 1:
            return samples[:, None].astype(np.float32, copy=False)

        if samples.shape[1] == 1:
            return samples.astype(np.float32, copy=False)

        left = samples[:, 0:1]
        right = samples[:, 1:2]
        summed = (left + right) * 0.5
        left_rms = float(np.sqrt(np.mean(left * left))) if left.size else 0.0
        right_rms = float(np.sqrt(np.mean(right * right))) if right.size else 0.0
        denom = left_rms * right_rms
        correlation = float(np.mean(left * right) / denom) if denom > 1e-9 else 1.0
        if correlation < 0.82:
            stronger = left if left_rms >= right_rms else right
            return (stronger * 0.75 + summed * 0.25).astype(np.float32, copy=False)
        return summed.astype(np.float32, copy=False)

    def _apply_voice_codec_bandpass(self, mono: np.ndarray) -> np.ndarray:
        if mono.size == 0:
            return mono

        samples = mono[:, 0].astype(np.float32, copy=False)
        spectrum = np.fft.rfft(samples)
        freqs = np.fft.rfftfreq(len(samples), 1.0 / self._sample_rate)
        mask = np.ones_like(freqs, dtype=np.float32)

        highpass_start = max(20.0, self._voice_codec_highpass_hz * 0.65)
        highpass_end = self._voice_codec_highpass_hz * 1.45
        lowpass_start = self._voice_codec_lowpass_hz
        lowpass_end = min(self._sample_rate / 2.0, self._voice_codec_lowpass_hz * 1.25)

        mask[freqs <= highpass_start] = 0.0
        highpass_ramp = (freqs > highpass_start) & (freqs < highpass_end)
        if np.any(highpass_ramp):
            phase = (freqs[highpass_ramp] - highpass_start) / (highpass_end - highpass_start)
            mask[highpass_ramp] *= 0.5 - 0.5 * np.cos(np.pi * phase)

        mask[freqs >= lowpass_end] = 0.0
        lowpass_ramp = (freqs > lowpass_start) & (freqs < lowpass_end)
        if np.any(lowpass_ramp):
            phase = (freqs[lowpass_ramp] - lowpass_start) / (lowpass_end - lowpass_start)
            mask[lowpass_ramp] *= 0.5 + 0.5 * np.cos(np.pi * phase)

        filtered = np.fft.irfft(spectrum * mask, n=len(samples)).astype(np.float32)
        return filtered[:, None]

    def _apply_virtual_limiter(self, mono: np.ndarray) -> np.ndarray:
        if mono.size == 0:
            self._virtual_limiter_gain = 1.0
            return mono

        mono = self._sanitize_samples(mono)
        if not np.isfinite(self._virtual_limiter_gain):
            self._virtual_limiter_gain = 1.0
        amplitudes = np.abs(mono[:, 0])
        target_gains = np.minimum(1.0, self._virtual_peak_ceiling / (amplitudes + 1e-9))
        smoothed_gains = np.empty_like(target_gains, dtype=np.float32)
        gain = self._virtual_limiter_gain

        for index, target_gain in enumerate(target_gains):
            if target_gain < gain:
                gain = float(target_gain)
            else:
                gain = (self._limiter_release_coeff * gain) + (
                    (1.0 - self._limiter_release_coeff) * float(target_gain)
                )
            smoothed_gains[index] = gain

        self._virtual_limiter_gain = gain
        limited = mono * smoothed_gains[:, None]
        return np.clip(limited, -self._virtual_peak_ceiling, self._virtual_peak_ceiling).astype(
            np.float32,
            copy=False,
        )

    def _apply_edge_fades(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) < 2:
            return samples

        fade_frames = min(len(samples) // 2, int(self._sample_rate * 0.003))
        if fade_frames <= 1:
            return samples

        faded = samples.copy()
        fade_in = np.linspace(0.0, 1.0, fade_frames, endpoint=True, dtype=np.float32)[:, None]
        fade_out = fade_in[::-1]
        faded[:fade_frames] *= fade_in
        faded[-fade_frames:] *= fade_out
        return faded

    def _apply_mic_noise_gate(self, mic_mono: np.ndarray) -> np.ndarray:
        if not self._mic_noise_gate_enabled or mic_mono.size == 0:
            self._mic_gate_level = 1.0
            self._mic_gate_hold_remaining = 0
            return mic_mono

        rms = float(np.sqrt(np.mean(mic_mono * mic_mono))) if mic_mono.size else 0.0
        threshold = 10 ** (self._mic_noise_gate_threshold_db / 20.0)
        previous_level = self._mic_gate_level

        if rms >= threshold:
            self._mic_gate_hold_remaining = self._mic_gate_hold_frames
            target_level = 1.0
        elif self._mic_gate_hold_remaining > 0:
            self._mic_gate_hold_remaining = max(0, self._mic_gate_hold_remaining - len(mic_mono))
            target_level = 1.0
        else:
            target_level = self._mic_gate_floor

        if target_level > previous_level:
            next_level = min(target_level, previous_level + 0.45)
        else:
            next_level = max(target_level, previous_level * 0.86)
        self._mic_gate_level = next_level

        if len(mic_mono) <= 1:
            return (mic_mono * next_level).astype(np.float32, copy=False)

        gain = np.linspace(previous_level, next_level, len(mic_mono), dtype=np.float32)[:, None]
        return (mic_mono * gain).astype(np.float32, copy=False)

    @staticmethod
    def _normalize_channels(samples: np.ndarray, target_channels: int) -> np.ndarray:
        if samples.ndim == 1:
            samples = samples[:, None]

        if samples.shape[1] == target_channels:
            return samples.astype(np.float32, copy=False)

        if samples.shape[1] == 1 and target_channels == 2:
            return np.repeat(samples, 2, axis=1).astype(np.float32, copy=False)

        if samples.shape[1] > target_channels:
            return samples[:, :target_channels].astype(np.float32, copy=False)

        if samples.shape[1] < target_channels:
            pad_width = target_channels - samples.shape[1]
            return np.pad(samples, ((0, 0), (0, pad_width))).astype(np.float32, copy=False)

        return samples.astype(np.float32, copy=False)

    @staticmethod
    def _downmix_to_mono(samples: np.ndarray) -> np.ndarray:
        if samples.ndim == 1:
            return samples[:, None].astype(np.float32, copy=False)

        if samples.shape[1] == 1:
            return samples.astype(np.float32, copy=False)

        if samples.shape[1] == 2:
            left = samples[:, 0:1]
            right = samples[:, 1:2]
            summed = (left + right) * 0.5
            left_rms = float(np.sqrt(np.mean(left * left))) if left.size else 0.0
            right_rms = float(np.sqrt(np.mean(right * right))) if right.size else 0.0
            best = left if left_rms >= right_rms else right
            best_rms = max(left_rms, right_rms)
            summed_rms = float(np.sqrt(np.mean(summed * summed))) if summed.size else 0.0
            if best_rms > 1e-7 and summed_rms < best_rms * 0.65:
                return best.astype(np.float32, copy=False)
            return summed.astype(np.float32, copy=False)

        return np.mean(samples, axis=1, keepdims=True, dtype=np.float32)

    @staticmethod
    def _resolve_index(device_id: str) -> int | None:
        if not device_id:
            return None
        try:
            return int(device_id)
        except ValueError:
            return None

    def _stop_stream_locked(self) -> None:
        self._set_audio_ready(False)

        duplex_stream = self._duplex_stream
        self._duplex_stream = None
        if duplex_stream is not None:
            try:
                duplex_stream.stop()
            except Exception:
                pass
            try:
                duplex_stream.close()
            except Exception:
                pass

        input_stream = self._input_stream
        self._input_stream = None
        if input_stream is not None:
            try:
                input_stream.stop()
            except Exception:
                pass
            try:
                input_stream.close()
            except Exception:
                pass

        monitor_stream = self._monitor_output_stream
        self._monitor_output_stream = None
        if monitor_stream is not None:
            try:
                monitor_stream.stop()
            except Exception:
                pass
            try:
                monitor_stream.close()
            except Exception:
                pass

        virtual_stream = self._virtual_output_stream
        self._virtual_output_stream = None
        self._virtual_output_device_label = ""
        if virtual_stream is not None:
            try:
                virtual_stream.stop()
            except Exception:
                pass
            try:
                virtual_stream.close()
            except Exception:
                pass

        self._reset_monitor_output_queue()
        self._reset_virtual_output_queue()

    def _push_monitor_output_frames(self, frames: np.ndarray) -> None:
        if self._monitor_output_stream is None or frames.size == 0:
            return

        chunk = self._normalize_channels(frames, self._output_channels).copy()
        with self._monitor_buffer_lock:
            self._monitor_chunks.append(chunk)
            self._monitor_buffer_frames += len(chunk)
            excess = self._monitor_buffer_frames - self._monitor_max_buffer_frames
            if excess > 0:
                self._drop_monitor_output_frames_locked(excess)

    def _pop_monitor_output_frames(self, frame_count: int, channel_count: int) -> np.ndarray:
        output = np.zeros((frame_count, self._output_channels), dtype=np.float32)
        write_offset = 0
        with self._monitor_buffer_lock:
            while write_offset < frame_count and self._monitor_chunks:
                chunk = self._monitor_chunks[0]
                take = min(frame_count - write_offset, len(chunk))
                output[write_offset : write_offset + take] = chunk[:take]
                write_offset += take
                self._monitor_buffer_frames -= take
                if take == len(chunk):
                    self._monitor_chunks.popleft()
                else:
                    self._monitor_chunks[0] = chunk[take:]
        return self._normalize_channels(output, channel_count)

    def _drop_monitor_output_frames_locked(self, frame_count: int) -> None:
        remaining = frame_count
        while remaining > 0 and self._monitor_chunks:
            chunk = self._monitor_chunks[0]
            take = min(remaining, len(chunk))
            remaining -= take
            self._monitor_buffer_frames -= take
            if take == len(chunk):
                self._monitor_chunks.popleft()
            else:
                self._monitor_chunks[0] = chunk[take:]

    def _reset_monitor_output_queue(self, *, prefill_frames: int = 0) -> None:
        with self._monitor_buffer_lock:
            self._monitor_chunks.clear()
            self._monitor_buffer_frames = 0
            if prefill_frames > 0:
                silence = np.zeros((prefill_frames, self._output_channels), dtype=np.float32)
                self._monitor_chunks.append(silence)
                self._monitor_buffer_frames = prefill_frames

    def _start_virtual_output_locked(self, *, excluded_output_index: int) -> str:
        virtual_output_index = self._find_virtual_cable_output_index(excluded_output_index)
        if virtual_output_index is None:
            return "Virtual mic route: signed cable output not detected"

        output_info = sd.query_devices(virtual_output_index)
        self._virtual_output_channels = max(1, min(2, int(output_info["max_output_channels"])))
        self._virtual_output_device_label = str(output_info["name"])
        profiles = (
            (
                self._virtual_output_blocksize,
                self._virtual_prefill_frames,
                self._virtual_output_latency_seconds,
                False,
            ),
            (
                self._virtual_fallback_blocksize,
                self._virtual_fallback_prefill_frames,
                self._virtual_fallback_latency_seconds,
                True,
            ),
        )
        errors: list[str] = []
        for blocksize, prefill_frames, latency_seconds, is_fallback in profiles:
            stream: sd.OutputStream | None = None
            try:
                self._reset_virtual_output_queue(prefill_frames=prefill_frames)
                stream = sd.OutputStream(
                    samplerate=self._sample_rate,
                    blocksize=blocksize,
                    dtype="float32",
                    channels=self._virtual_output_channels,
                    device=virtual_output_index,
                    latency=latency_seconds,
                    callback=self._virtual_output_callback,
                    prime_output_buffers_using_stream_callback=True,
                )
                # Publish before start so mic callbacks can fill the safety queue immediately.
                self._virtual_output_stream = stream
                stream.start()
                suffix = " (compatibility buffering)" if is_fallback else ""
                return f"Virtual mic active: {self._virtual_output_device_label}{suffix}"
            except Exception as exc:
                errors.append(str(exc))
                self._virtual_output_stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

        self._reset_virtual_output_queue()
        self._virtual_output_device_label = ""
        return f"Virtual mic route failed: {'; '.join(errors)}"

    def _virtual_output_callback(self, outdata, frames, time_info, status) -> None:
        del time_info
        if status:
            self._report_stream_status("Virtual output status", status)
        outdata[:] = self._pop_virtual_output_frames(frames, outdata.shape[1])

    def _push_virtual_output_frames(self, frames: np.ndarray) -> None:
        if self._virtual_output_stream is None or frames.size == 0:
            return

        chunk = self._normalize_channels(frames, self._virtual_output_channels).copy()
        with self._virtual_buffer_lock:
            self._virtual_chunks.append(chunk)
            self._virtual_buffer_frames += len(chunk)
            excess = self._virtual_buffer_frames - self._virtual_max_buffer_frames
            if excess > 0:
                self._drop_virtual_output_frames_locked(excess)

    def _pop_virtual_output_frames(self, frame_count: int, channel_count: int) -> np.ndarray:
        with self._virtual_buffer_lock:
            source = self._take_virtual_output_frames_locked(frame_count)

        source = self._normalize_channels(source, channel_count)
        return source

    def _take_virtual_output_frames_locked(self, frame_count: int) -> np.ndarray:
        output = np.zeros((frame_count, self._virtual_output_channels), dtype=np.float32)
        write_offset = 0
        while write_offset < frame_count and self._virtual_chunks:
            chunk = self._virtual_chunks[0]
            take = min(frame_count - write_offset, len(chunk))
            output[write_offset : write_offset + take] = chunk[:take]
            write_offset += take
            self._virtual_buffer_frames -= take
            if take == len(chunk):
                self._virtual_chunks.popleft()
            else:
                self._virtual_chunks[0] = chunk[take:]
        return output

    def _drop_virtual_output_frames_locked(self, frame_count: int) -> None:
        remaining = frame_count
        while remaining > 0 and self._virtual_chunks:
            chunk = self._virtual_chunks[0]
            take = min(remaining, len(chunk))
            remaining -= take
            self._virtual_buffer_frames -= take
            if take == len(chunk):
                self._virtual_chunks.popleft()
            else:
                self._virtual_chunks[0] = chunk[take:]

    def _reset_virtual_output_queue(self, *, prefill_frames: int = 0) -> None:
        with self._virtual_buffer_lock:
            self._virtual_chunks.clear()
            self._virtual_buffer_frames = 0
            if prefill_frames > 0:
                silence = np.zeros(
                    (prefill_frames, self._virtual_output_channels),
                    dtype=np.float32,
                )
                self._virtual_chunks.append(silence)
                self._virtual_buffer_frames = prefill_frames

    def _find_virtual_cable_output_index(self, excluded_output_index: int) -> int | None:
        candidates = []
        hostapis = sd.query_hostapis()
        for index, device in enumerate(sd.query_devices()):
            if index == excluded_output_index or device["max_output_channels"] <= 0:
                continue

            hostapi_name = hostapis[int(device["hostapi"])]["name"]
            display_name = f'{device["name"]} ({hostapi_name})'
            if self._looks_like_virtual_cable_output(display_name):
                candidates.append(
                    (
                        self._virtual_cable_output_rank(display_name),
                        0 if int(device["max_output_channels"]) <= 2 else 1,
                        self._hostapi_priority(hostapi_name),
                        index,
                    )
                )

        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[:3])[0][3]

    def _visible_devices(self, *, is_input: bool) -> list[tuple[str, str]]:
        candidates = self._device_candidates(is_input=is_input)
        visible = [candidate for candidate in candidates if self._is_user_visible_device(candidate, is_input=is_input)]
        verified = [candidate for candidate in visible if self._device_is_usable(candidate, is_input=is_input)]

        best = self._dedupe_device_candidates(verified)

        def display_order(candidate: DeviceCandidate) -> tuple[int, str]:
            label = f"{candidate.name} ({candidate.hostapi_name})"
            preferred = self._looks_like_real_microphone(label) if is_input else self._looks_like_real_monitor(label)
            return (0 if preferred else 1, candidate.display_name.lower())

        ordered = sorted(best, key=display_order)
        name_counts: dict[str, int] = {}
        for candidate in ordered:
            key = candidate.display_name.casefold()
            name_counts[key] = name_counts.get(key, 0) + 1

        name_positions: dict[str, int] = {}
        devices: list[tuple[str, str]] = []
        for candidate in ordered:
            label = candidate.display_name
            key = label.casefold()
            if name_counts[key] > 1:
                name_positions[key] = name_positions.get(key, 0) + 1
                label = f"{label} ({name_positions[key]})"
            devices.append((str(candidate.index), label))
        return devices

    def _device_candidates(self, *, is_input: bool) -> list[DeviceCandidate]:
        hostapis = sd.query_hostapis()
        candidates: list[DeviceCandidate] = []

        for index, device in enumerate(sd.query_devices()):
            max_channels = device["max_input_channels"] if is_input else device["max_output_channels"]
            if max_channels <= 0:
                continue

            hostapi_name = hostapis[int(device["hostapi"])]["name"]
            name = str(device["name"]).strip()
            candidates.append(DeviceCandidate(index=index, name=name, hostapi_name=hostapi_name, max_channels=int(max_channels)))

        return candidates

    def _dedupe_device_candidates(self, candidates: list[DeviceCandidate]) -> list[DeviceCandidate]:
        best: list[DeviceCandidate] = []
        ordered = sorted(candidates, key=lambda candidate: (self._hostapi_priority(candidate.hostapi_name), candidate.index))
        for candidate in ordered:
            if any(self._same_physical_device(candidate, current) for current in best):
                continue
            best.append(candidate)
        return best

    def _device_is_usable(self, candidate: DeviceCandidate, *, is_input: bool) -> bool:
        return self._supported_channel_count(
            candidate.index,
            candidate.max_channels,
            is_input=is_input,
        ) is not None

    def _supported_channel_count(
        self,
        device_index: int,
        max_channels: int,
        *,
        is_input: bool,
    ) -> int | None:
        for channels in range(max(0, min(2, int(max_channels))), 0, -1):
            try:
                if is_input:
                    sd.check_input_settings(
                        device=device_index,
                        channels=channels,
                        samplerate=self._sample_rate,
                        dtype="float32",
                    )
                else:
                    sd.check_output_settings(
                        device=device_index,
                        channels=channels,
                        samplerate=self._sample_rate,
                        dtype="float32",
                    )
                return channels
            except Exception:
                continue
        return None

    def _is_user_visible_device(self, candidate: DeviceCandidate, *, is_input: bool) -> bool:
        del is_input
        label = f"{candidate.name} ({candidate.hostapi_name})"
        return not self._looks_virtual(label) and not self._looks_like_hidden_audio_alias(label)

    def _is_safe_fallback_device(self, candidate: DeviceCandidate) -> bool:
        label = f"{candidate.name} ({candidate.hostapi_name})"
        return not self._looks_virtual(label) and not self._looks_like_hidden_audio_alias(label)

    @classmethod
    def _same_physical_device(cls, left: DeviceCandidate, right: DeviceCandidate) -> bool:
        left_name = cls._device_key(left.name)
        right_name = cls._device_key(right.name)
        same_hostapi = left.hostapi_name.casefold() == right.hostapi_name.casefold()
        if left_name == right_name:
            return not same_hostapi

        if same_hostapi:
            return False

        # PortAudio's MME backend truncates long Windows endpoint names. Treat a
        # long matching prefix as the same endpoint and retain the better API.
        if "mme" not in left.hostapi_name.lower() and "mme" not in right.hostapi_name.lower():
            return False
        shorter, longer = sorted((left_name, right_name), key=len)
        return len(shorter) >= 28 and longer.startswith(shorter)

    @staticmethod
    def _hostapi_priority(hostapi_name: str) -> int:
        lowered = hostapi_name.lower()
        if "wasapi" in lowered:
            return 0
        if "wdm" in lowered:
            return 1
        if "directsound" in lowered:
            return 2
        if "mme" in lowered:
            return 3
        return 9

    @staticmethod
    def _is_wasapi_hostapi(hostapi_name: str) -> bool:
        return "wasapi" in hostapi_name.lower()

    @staticmethod
    def _device_key(device_name: str) -> str:
        lowered = device_name.strip().lower()
        return " ".join(lowered.split())

    @staticmethod
    def _looks_virtual(device_label: str) -> bool:
        lowered = device_label.lower()
        return any(
            keyword in lowered
            for keyword in (
                "cable",
                "virtual",
                "voicemeeter",
                "vb-audio",
                "vb audio",
                "audio point",
                "steam streaming",
                "streaming speakers",
                "streaming microphone",
            )
        )

    @staticmethod
    def _looks_like_hidden_audio_alias(device_label: str) -> bool:
        lowered = device_label.lower()
        return any(
            keyword in lowered
            for keyword in (
                "sound mapper",
                "primary sound",
                "output ()",
                "input ()",
                "stereo mix",
                "what u hear",
                "loopback",
                "wave)",
            )
        )

    @staticmethod
    def _sanitize_samples(samples: np.ndarray) -> np.ndarray:
        normalized = np.asarray(samples, dtype=np.float32)
        if np.isfinite(normalized).all():
            return normalized
        return np.nan_to_num(
            normalized,
            copy=True,
            nan=0.0,
            posinf=1.0,
            neginf=-1.0,
        ).astype(np.float32, copy=False)

    @staticmethod
    def _looks_like_virtual_cable_output(device_label: str) -> bool:
        lowered = device_label.lower()
        return (
            "cable input" in lowered
            or "cable in 16ch" in lowered
            or "voicemeeter input" in lowered
            or "vb-audio virtual cable" in lowered
        )

    @staticmethod
    def _virtual_cable_output_rank(device_label: str) -> int:
        lowered = device_label.lower()
        if "cable input" in lowered and "16ch" not in lowered:
            return 0
        if "cable input" in lowered:
            return 1
        if "cable in 16ch" in lowered:
            return 8
        if "voicemeeter input" in lowered:
            return 20
        return 30

    @staticmethod
    def _looks_like_real_microphone(device_label: str) -> bool:
        lowered = device_label.lower()
        return any(keyword in lowered for keyword in ("microphone", "mic", "headset"))

    @staticmethod
    def _looks_like_real_monitor(device_label: str) -> bool:
        lowered = device_label.lower()
        return any(
            keyword in lowered
            for keyword in (
                "speaker",
                "speakers",
                "headphone",
                "headphones",
                "headset",
                "monitor",
                "display",
                "hdmi",
                "high definition audio",
                "audio",
            )
        )
