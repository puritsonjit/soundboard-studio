from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import uuid
import wave
from dataclasses import asdict, fields
from pathlib import Path

from .models import AppConfig, PadConfig, default_pads


class ConfigManager:
    def __init__(self, config_path: Path, sounds_dir: Path) -> None:
        self._config_path = config_path
        self._sounds_dir = sounds_dir
        self.last_warning = ""

    def load(self) -> AppConfig:
        if not self._config_path.exists():
            config = AppConfig(pads=self._default_pads_with_demo())
            self.save(config)
            return config

        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("The settings file root must be an object.")
            raw_pads = raw.get("pads", [])
            pads = self._load_pads(raw_pads)

            config = AppConfig(
                server_port=self._clamp_int(raw.get("server_port", 8765), 1024, 65535, 8765),
                selected_input_device_id=str(raw.get("selected_input_device_id") or ""),
                selected_input_device_name=str(raw.get("selected_input_device_name") or ""),
                selected_output_device_id=str(raw.get("selected_output_device_id") or ""),
                selected_output_device_name=str(raw.get("selected_output_device_name") or ""),
                monitor_muted=self._coerce_bool(raw.get("monitor_muted", False), False),
                playback_volume_percent=self._clamp_int(raw.get("playback_volume_percent", 100), 0, 200, 100),
                mic_noise_gate_enabled=self._coerce_bool(raw.get("mic_noise_gate_enabled", True), True),
                mic_noise_gate_threshold_db=self._clamp_float(
                    raw.get("mic_noise_gate_threshold_db", -42.0),
                    -70.0,
                    -20.0,
                    -42.0,
                ),
                raw_hd_soundboard_enabled=self._coerce_bool(raw.get("raw_hd_soundboard_enabled", False), False),
                remote_playback_mode=self._normalize_remote_playback_mode(
                    raw.get("remote_playback_mode", "layer")
                ),
                remote_connection_mode=self._normalize_remote_connection_mode(
                    raw.get("remote_connection_mode", "local")
                ),
                pads=pads,
            )
        except Exception as exc:
            backup_path = self._backup_invalid_config()
            config = AppConfig(pads=self._default_pads_with_demo())
            self.save(config)
            backup_note = f" A backup was kept at {backup_path}." if backup_path is not None else ""
            self.last_warning = f"Settings were reset because the file could not be read: {exc}.{backup_note}".strip()
            return config

        try:
            self.save(config)
        except OSError as exc:
            # Keep a valid loaded profile when only its normalized form cannot be written.
            self.last_warning = f"Settings loaded, but updates could not be written: {exc}"
        return config

    def save(self, config: AppConfig) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(config)
        temporary_path = self._config_path.with_name(f".{self._config_path.name}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._config_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _default_pads_with_demo(self) -> list[PadConfig]:
        pads = default_pads()
        demo_path = self._ensure_demo_sound()
        if pads[0].label == "Pad 1":
            pads[0].label = "Demo Beep"
        pads[0].sound_path = str(demo_path)
        return pads

    def _ensure_demo_sound(self) -> Path:
        self._sounds_dir.mkdir(parents=True, exist_ok=True)
        demo_path = self._sounds_dir / "demo_beep.wav"
        if demo_path.exists():
            return demo_path

        sample_rate = 44100
        duration_seconds = 0.25
        frequency = 880.0
        amplitude = 0.35
        frame_count = int(sample_rate * duration_seconds)

        with wave.open(str(demo_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for frame_index in range(frame_count):
                t = frame_index / sample_rate
                sample = int(32767 * amplitude * math.sin(2 * math.pi * frequency * t))
                frames.extend(struct.pack("<h", sample))
            wav_file.writeframes(bytes(frames))

        return demo_path

    def _load_pads(self, raw_pads: object) -> list[PadConfig]:
        defaults = default_pads()
        pads = list(defaults)
        if not isinstance(raw_pads, list):
            return pads

        for index, item in enumerate(raw_pads[: len(defaults)]):
            pads[index] = self._load_pad(item, defaults[index])
        return pads

    def _backup_invalid_config(self) -> Path | None:
        if not self._config_path.is_file():
            return None
        backup_path = self._config_path.with_name(
            f"{self._config_path.stem}.invalid-{uuid.uuid4().hex[:8]}{self._config_path.suffix}"
        )
        try:
            shutil.copy2(self._config_path, backup_path)
            return backup_path
        except OSError:
            return None

    @classmethod
    def _load_pad(cls, item: object, fallback: PadConfig) -> PadConfig:
        if not isinstance(item, dict):
            return fallback

        field_names = {field.name for field in fields(PadConfig)}
        payload = {name: item.get(name, getattr(fallback, name)) for name in field_names}
        payload["id"] = fallback.id
        payload["label"] = str(payload.get("label") or fallback.label)[:48]
        payload["sound_path"] = str(payload.get("sound_path") or "")
        payload["sound_mode"] = cls._normalize_sound_mode(payload.get("sound_mode"))
        payload["sound_pack_path"] = str(payload.get("sound_pack_path") or "")
        payload["image_path"] = str(payload.get("image_path") or "")
        payload["color_hex"] = cls._normalize_color_hex(payload.get("color_hex"), fallback.color_hex)
        payload["volume_percent"] = cls._clamp_int(
            payload.get("volume_percent", fallback.volume_percent),
            0,
            200,
            fallback.volume_percent,
        )
        payload["hotkey"] = cls._normalize_hotkey(payload.get("hotkey"))
        return PadConfig(**payload)

    @staticmethod
    def _clamp_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            coerced = fallback
        return max(minimum, min(maximum, coerced))

    @staticmethod
    def _clamp_float(value: object, minimum: float, maximum: float, fallback: float) -> float:
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            coerced = fallback
        if not math.isfinite(coerced):
            coerced = fallback
        return max(minimum, min(maximum, coerced))

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
    def _normalize_color_hex(value: object, fallback: str) -> str:
        color = str(value or "").strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            return color.upper()
        return fallback

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
    def _normalize_hotkey(value: object) -> str:
        hotkey = str(value or "").strip()
        return hotkey[:80]
