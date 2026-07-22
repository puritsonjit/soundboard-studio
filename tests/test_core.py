from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "python_qt6"))

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer
import numpy as np
from PySide6.QtCore import QEvent, QLocale, QPoint, QRect, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget

from soundboard_qt6.app import configure_display_locale
from soundboard_qt6.asset_store import RuntimeAssetStore
from soundboard_qt6.audio_engine import DeviceCandidate, PreparedSound, QtAudioEngine
from soundboard_qt6.clip_editor import (
    SoundClipEditorDialog,
    SoundClipError,
    export_audio_clip,
    read_clip_metadata,
)
from soundboard_qt6.config_manager import ConfigManager
from soundboard_qt6.hotkey_manager import (
    HotkeyError,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    describe_hotkey_key_source,
    normalize_hotkey,
    parse_hotkey,
)
from soundboard_qt6.internet_tunnel import InternetTunnel, extract_public_url
from soundboard_qt6.main_window import MainWindow, SoundValidationDialog
from soundboard_qt6.models import PAD_COUNT, AppConfig, PadConfig, default_pads
from soundboard_qt6.paths import AppPaths
from soundboard_qt6.remote_server import BrowserRemoteServer
from soundboard_qt6.sound_validation import (
    SoundValidationError,
    scan_sound_pack,
    sound_pack_files,
    validate_sound_duration,
    validate_sound_pack,
)
from soundboard_qt6.single_instance import SingleInstanceGuard
from soundboard_qt6.windows_security import is_authenticode_signature_valid


class DisplayLocaleTests(unittest.TestCase):
    def test_application_uses_portable_ascii_digits(self) -> None:
        original_locale = QLocale()
        try:
            QLocale.setDefault(QLocale(QLocale.Language.Arabic, QLocale.Country.Egypt))
            self.assertNotEqual(QLocale().zeroDigit(), "0")

            configure_display_locale()

            self.assertEqual(QLocale().zeroDigit(), "0")
            self.assertEqual(QLocale().decimalPoint(), ".")
            self.assertEqual(QLocale().toString(1234.5, "f", 1), "1234.5")
        finally:
            QLocale.setDefault(original_locale)

    def test_clip_time_fields_always_use_ascii_digits(self) -> None:
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        dialog = SoundClipEditorDialog(parent)
        try:
            dialog.start_spin.setValue(12.5)
            self.assertEqual(dialog.start_spin.locale().name(), "C")
            self.assertEqual(dialog.start_spin.text(), "12.50 s")
        finally:
            dialog.close()
            dialog.cleanup()
            parent.close()
            app.processEvents()


class RuntimeAssetStoreTests(unittest.TestCase):
    def test_sound_taken_from_another_managed_pad_gets_an_independent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_pack = root / "source-pack"
            source_pack.mkdir()
            source = source_pack / "effect.wav"
            source.write_bytes(b"sound data")
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            managed_pack = store.copy_sound_pack(str(source_pack), "pad-06", [source])
            pack_sound = next(managed_pack.iterdir())

            independent = store.copy_sound(str(pack_sound), "pad-04")

            self.assertNotEqual(independent, pack_sound)
            self.assertEqual(independent.parent, store.sounds_dir.resolve())
            self.assertTrue(independent.name.startswith("pad-04-"))
            self.assertEqual(independent.read_bytes(), pack_sound.read_bytes())
            self.assertTrue(store.remove_if_managed(str(managed_pack)))
            self.assertTrue(independent.is_file())

    def test_same_named_sources_do_not_overwrite_saved_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            first_source = root / "first" / "effect.mp3"
            second_source = root / "second" / "effect.mp3"
            first_source.parent.mkdir()
            second_source.parent.mkdir()
            first_source.write_bytes(b"first sound")
            second_source.write_bytes(b"second sound")

            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            first_copy = store.copy_sound(str(first_source), "pad-01")
            second_copy = store.copy_sound(str(second_source), "pad-01")

            self.assertNotEqual(first_copy, second_copy)
            self.assertEqual(first_copy.read_bytes(), b"first sound")
            self.assertEqual(second_copy.read_bytes(), b"second sound")

    def test_damaged_existing_managed_copy_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "effect.wav"
            source.write_bytes(b"known-good-sound")
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            managed = store.copy_sound(str(source), "pad-01")
            managed.write_bytes(b"damaged")

            repaired = store.copy_sound(str(source), "pad-01")

            self.assertEqual(repaired, managed)
            self.assertEqual(repaired.read_bytes(), b"known-good-sound")

    def test_sound_pack_is_copied_under_its_pad_without_overwriting_the_previous_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source-pack"
            source.mkdir()
            first = source / "first.wav"
            second = source / "second.mp3"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")

            first_pack = store.copy_sound_pack(str(source), "pad-04", [first, second])
            second.write_bytes(b"updated second")
            second_pack = store.copy_sound_pack(str(source), "pad-04", [first, second])
            copied_to_another_pad = store.copy_sound_pack(
                str(second_pack),
                "pad-05",
                list(second_pack.iterdir()),
            )

            self.assertEqual(first_pack.parent.name, "pad-04")
            self.assertEqual(second_pack.parent.name, "pad-04")
            self.assertNotEqual(first_pack, second_pack)
            self.assertEqual(copied_to_another_pad.parent.name, "pad-05")
            self.assertNotEqual(copied_to_another_pad, second_pack)
            self.assertEqual(len(list(first_pack.iterdir())), 2)
            self.assertEqual(len(list(second_pack.iterdir())), 2)
            self.assertTrue(store.remove_if_managed(str(first_pack)))
            self.assertFalse(first_pack.exists())
            self.assertTrue(second_pack.is_dir())

    def test_managed_sound_pack_is_rebuilt_when_only_a_valid_subset_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            managed_source = root / "runtime" / "sounds" / "pad-04" / "pack-manual"
            managed_source.mkdir(parents=True)
            valid = managed_source / "valid.wav"
            rejected = managed_source / "too-long.wav"
            valid.write_bytes(b"valid")
            rejected.write_bytes(b"rejected")

            copied = store.copy_sound_pack(str(managed_source), "pad-04", [valid])

            self.assertNotEqual(copied, managed_source)
            self.assertEqual(len(list(copied.iterdir())), 1)
            self.assertTrue(next(copied.iterdir()).name.startswith("valid-"))

    def test_managed_sound_pack_rebuild_removes_unexpected_nested_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source"
            source.mkdir()
            import soundfile as sf

            sf.write(source / "valid.wav", np.zeros(8_000, dtype=np.float32), 8_000)
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            valid_files = validate_sound_pack(source)
            managed = store.copy_sound_pack(str(source), "pad-01", valid_files)
            injected_dir = managed / "manual"
            injected_dir.mkdir()
            sf.write(injected_dir / "too-long.wav", np.zeros(8_000 * 16), 8_000)

            rebuilt = store.copy_sound_pack(str(managed), "pad-01", valid_files)

            self.assertEqual(rebuilt, managed)
            self.assertFalse(injected_dir.exists())
            self.assertEqual(len(validate_sound_pack(rebuilt)), 1)

    def test_sound_pack_repairs_a_destination_replaced_by_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source"
            source.mkdir()
            original = source / "valid.wav"
            original.write_bytes(b"valid sound")
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            managed = store.copy_sound_pack(str(source), "pad-01", [original])
            shutil_target = managed
            import shutil

            shutil.rmtree(shutil_target)
            shutil_target.write_bytes(b"damaged pack path")

            repaired = store.copy_sound_pack(str(source), "pad-01", [original])

            self.assertEqual(repaired, managed)
            self.assertTrue(repaired.is_dir())
            self.assertEqual(len(list(repaired.iterdir())), 1)
            self.assertEqual(list(repaired.parent.glob("*.backup")), [])


class AudioClipEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_oversized_source_is_trimmed_to_24_bit_wav_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "long-source.wav"
            target = root / "edited" / "long-source-clip.wav"
            sample_rate = 8_000
            source_samples = np.linspace(-0.75, 0.75, sample_rate * 20, dtype=np.float32)
            import soundfile as sf

            sf.write(source, source_samples, sample_rate, subtype="FLOAT")
            source_bytes = source.read_bytes()

            result = export_audio_clip(source, target, 2.0, 14.5)
            result_info = sf.info(result)

            self.assertAlmostEqual(result_info.duration, 12.5, places=3)
            self.assertEqual(result_info.subtype, "PCM_24")
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertAlmostEqual(read_clip_metadata(source).duration_seconds, 20.0, places=3)

    def test_clip_export_cannot_exceed_permanent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "long-source.wav"
            import soundfile as sf

            sf.write(source, np.zeros(8_000 * 20, dtype=np.float32), 8_000)
            with self.assertRaisesRegex(SoundClipError, "15 seconds or shorter"):
                export_audio_clip(source, root / "invalid.wav", 0.0, 15.1)

    def test_dialog_opens_long_audio_with_fifteen_second_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source = Path(temporary_dir) / "long-source.wav"
            import soundfile as sf

            sf.write(source, np.zeros(8_000 * 20, dtype=np.float32), 8_000)
            parent = QWidget()
            dialog = SoundClipEditorDialog(parent, str(source))

            self.assertTrue(dialog.drop_zone.acceptDrops())
            self.assertTrue(dialog.use_button.isEnabled())
            self.assertAlmostEqual(dialog.end_spin.value() - dialog.start_spin.value(), 15.0)
            self.assertEqual(dialog.selection_label.text(), "15.0 sec selected")

            dialog.close()
            dialog.cleanup()
            parent.close()

    def test_replacing_short_audio_with_long_audio_updates_time_ranges_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            short_source = root / "short.wav"
            long_source = root / "long.wav"
            import soundfile as sf

            sf.write(short_source, np.zeros(8_000 * 2, dtype=np.float32), 8_000)
            sf.write(long_source, np.zeros(8_000 * 20, dtype=np.float32), 8_000)
            parent = QWidget()
            dialog = SoundClipEditorDialog(parent, str(short_source))

            dialog.load_file(str(long_source))

            self.assertAlmostEqual(dialog.end_spin.maximum(), 20.0)
            self.assertAlmostEqual(dialog.end_spin.value(), 15.0)
            self.assertEqual(dialog.selection_label.text(), "15.0 sec selected")
            dialog.close()
            dialog.cleanup()
            parent.close()

    def test_failed_replacement_clears_the_previous_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "valid.wav"
            invalid = root / "invalid.mp3"
            import soundfile as sf

            sf.write(source, np.zeros(8_000, dtype=np.float32), 8_000)
            invalid.write_text("not audio", encoding="utf-8")
            parent = QWidget()
            dialog = SoundClipEditorDialog(parent, str(source))

            dialog.load_file(str(invalid))

            self.assertIsNone(dialog._metadata)
            self.assertFalse(dialog.use_button.isEnabled())
            self.assertFalse(dialog.preview_button.isEnabled())
            self.assertEqual(dialog.selection_label.text(), "0.0 sec selected")
            dialog.close()
            dialog.cleanup()
            parent.close()

    def test_empty_audio_is_rejected_by_permanent_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source = Path(temporary_dir) / "empty.wav"
            import soundfile as sf

            sf.write(source, np.zeros(0, dtype=np.float32), 8_000)
            with self.assertRaisesRegex(SoundValidationError, "empty or unreadable"):
                validate_sound_duration(source)

    def test_clip_export_replaces_non_finite_audio_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "non-finite.wav"
            target = root / "clip.wav"
            import soundfile as sf

            samples = np.zeros((16_000, 2), dtype=np.float32)
            samples[100, 0] = np.nan
            samples[200, 1] = np.inf
            sf.write(source, samples, 8_000, format="WAV", subtype="FLOAT")

            export_audio_clip(source, target, 0.0, 1.0)
            exported, _sample_rate = sf.read(target, dtype="float32", always_2d=True)

            self.assertTrue(np.isfinite(exported).all())

    def test_sound_pack_rejects_any_clip_over_the_permanent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            import soundfile as sf

            sf.write(root / "valid.wav", np.zeros(8_000), 8_000)
            sf.write(root / "too-long.wav", np.zeros(8_000 * 16), 8_000)
            (root / "notes.txt").write_text("ignored", encoding="utf-8")

            with self.assertRaisesRegex(SoundValidationError, "too-long.wav"):
                validate_sound_pack(root)

    def test_sound_pack_scan_separates_valid_and_over_limit_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            import soundfile as sf

            sf.write(root / "valid.wav", np.zeros(8_000), 8_000)
            sf.write(root / "too-long.wav", np.zeros(8_000 * 16), 8_000)

            result = scan_sound_pack(root)

            self.assertEqual([path.name for path in result.accepted], ["valid.wav"])
            self.assertEqual([item.path.name for item in result.rejected], ["too-long.wav"])
            self.assertIn("16 seconds", result.rejected[0].reason)

    def test_empty_sound_pack_path_never_scans_the_working_directory(self) -> None:
        self.assertEqual(sound_pack_files(""), [])
        self.assertEqual(sound_pack_files("   "), [])
        with self.assertRaisesRegex(SoundValidationError, "Choose a folder"):
            validate_sound_pack("")


class ConfigManagerTests(unittest.TestCase):
    def test_load_normalizes_boolean_color_and_internal_pad_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            config_path.parent.mkdir()
            pads = [
                {
                    "id": "duplicate-id",
                    "label": pad.label,
                    "sound_path": "assigned.wav" if index == 0 else "",
                    "image_path": "",
                    "color_hex": "#GGGGGG" if index == 0 else pad.color_hex,
                    "volume_percent": 100,
                    "hotkey": "Ctrl+Shift+1" if index == 0 else "",
                }
                for index, pad in enumerate(default_pads())
            ]
            config_path.write_text(
                json.dumps(
                    {
                        "monitor_muted": "false",
                        "mic_noise_gate_enabled": "false",
                        "raw_hd_soundboard_enabled": "true",
                        "remote_connection_mode": "internet",
                        "pads": pads,
                    }
                ),
                encoding="utf-8",
            )

            config = ConfigManager(config_path, root / "runtime" / "sounds").load()

            self.assertFalse(config.monitor_muted)
            self.assertFalse(config.mic_noise_gate_enabled)
            self.assertTrue(config.raw_hd_soundboard_enabled)
            self.assertEqual(config.remote_connection_mode, "internet")
            self.assertEqual(
                [pad.id for pad in config.pads],
                [f"pad-{index:02d}" for index in range(1, PAD_COUNT + 1)],
            )
            self.assertEqual(config.pads[0].color_hex, "#FF7043")
            self.assertEqual(config.pads[0].hotkey, "Ctrl+Shift+1")

    def test_load_migrates_existing_pads_and_normalizes_sound_pack_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        "pads": [
                            {
                                "label": "Random",
                                "sound_path": "single.wav",
                                "sound_mode": "PACK",
                                "sound_pack_path": "managed-pack",
                            },
                            {"label": "Legacy", "sound_path": "legacy.wav"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            config = ConfigManager(config_path, root / "runtime" / "sounds").load()

            self.assertEqual(config.pads[0].sound_mode, "pack")
            self.assertEqual(config.pads[0].sound_pack_path, "managed-pack")
            self.assertEqual(config.pads[1].sound_mode, "single")
            self.assertEqual(config.pads[1].sound_pack_path, "")

    def test_save_replaces_config_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            manager = ConfigManager(config_path, root / "runtime" / "sounds")
            manager.save(AppConfig(pads=default_pads()))

            self.assertEqual(
                len(json.loads(config_path.read_text(encoding="utf-8"))["pads"]),
                PAD_COUNT,
            )
            self.assertFalse(config_path.with_name(f".{config_path.name}.tmp").exists())

    def test_partial_pad_config_preserves_existing_pad_and_fills_missing_slots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps({"pads": [{"label": "Keep me", "sound_path": "assigned.wav"}]}),
                encoding="utf-8",
            )

            config = ConfigManager(config_path, root / "runtime" / "sounds").load()

            self.assertEqual(len(config.pads), PAD_COUNT)
            self.assertEqual(config.pads[0].label, "Keep me")
            self.assertEqual(config.pads[0].sound_path, "assigned.wav")
            self.assertEqual(config.pads[-1].id, "pad-24")

    def test_intentionally_empty_profile_stays_empty_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            manager = ConfigManager(config_path, root / "runtime" / "sounds")
            manager.save(AppConfig(pads=default_pads()))

            config = manager.load()

            self.assertTrue(all(not pad.sound_path for pad in config.pads))
            self.assertEqual(config.pads[0].label, "Pad 1")

    def test_invalid_config_is_backed_up_before_defaults_are_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            config_path.parent.mkdir()
            config_path.write_text("{broken json", encoding="utf-8")
            manager = ConfigManager(config_path, root / "runtime" / "sounds")

            config = manager.load()

            backups = list(config_path.parent.glob("soundboard_config.invalid-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken json")
            self.assertEqual(len(config.pads), PAD_COUNT)
            self.assertIn("backup", manager.last_warning.lower())

    def test_valid_config_is_not_discarded_when_normalized_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "data" / "soundboard_config.json"
            config_path.parent.mkdir()
            pads = [
                {
                    "label": "Keep loaded profile" if index == 0 else pad.label,
                    "sound_path": "assigned.wav" if index == 0 else "",
                }
                for index, pad in enumerate(default_pads())
            ]
            config_path.write_text(json.dumps({"pads": pads}), encoding="utf-8")
            manager = ConfigManager(config_path, root / "runtime" / "sounds")

            with patch.object(manager, "save", side_effect=OSError("disk full")):
                config = manager.load()

            self.assertEqual(config.pads[0].label, "Keep loaded profile")
            self.assertIn("disk full", manager.last_warning)


class AudioEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = QtAudioEngine()

    def tearDown(self) -> None:
        self.engine.shutdown()

    def test_restart_reports_device_failure_without_raising(self) -> None:
        statuses: list[str] = []
        self.engine.status_changed.connect(statuses.append)
        self.engine._input_device_id = "1"
        self.engine._output_device_id = "2"

        with patch("soundboard_qt6.audio_engine.sd.query_devices", side_effect=OSError("device busy")):
            self.engine.restart_stream()

        self.assertTrue(any("Mixer unavailable: device busy" in status for status in statuses))

    def test_device_list_falls_back_to_a_verified_non_wasapi_device(self) -> None:
        wasapi = DeviceCandidate(1, "Microphone One", "Windows WASAPI", 1)
        directsound = DeviceCandidate(2, "Microphone Two", "Windows DirectSound", 1)

        with (
            patch.object(self.engine, "_device_candidates", return_value=[wasapi, directsound]),
            patch.object(self.engine, "_device_is_usable", side_effect=lambda candidate, **_: candidate is directsound),
        ):
            devices = self.engine.available_input_devices()

        self.assertEqual(devices, [("2", "Microphone Two")])

    def test_device_list_keeps_working_non_wasapi_endpoint_beside_wasapi(self) -> None:
        wasapi = DeviceCandidate(1, "Built-in Speakers", "Windows WASAPI", 2)
        logitech = DeviceCandidate(2, "Logitech PRO X 2 LIGHTSPEED", "Windows DirectSound", 2)

        with (
            patch.object(self.engine, "_device_candidates", return_value=[wasapi, logitech]),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_output_devices()

        self.assertEqual(
            devices,
            [("1", "Built-in Speakers"), ("2", "Logitech PRO X 2 LIGHTSPEED")],
        )

    def test_same_named_real_devices_remain_selectable_without_api_aliases(self) -> None:
        first = DeviceCandidate(1, "USB Audio Device", "Windows WASAPI", 2)
        second = DeviceCandidate(2, "USB Audio Device", "Windows WASAPI", 2)
        duplicate_alias = DeviceCandidate(3, "USB Audio Device", "Windows DirectSound", 2)

        with (
            patch.object(
                self.engine,
                "_device_candidates",
                return_value=[first, second, duplicate_alias],
            ),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_output_devices()

        self.assertEqual(
            devices,
            [("1", "USB Audio Device (1)"), ("2", "USB Audio Device (2)")],
        )

    def test_device_list_hides_virtual_and_system_aliases(self) -> None:
        candidates = [
            DeviceCandidate(1, "Logitech PRO X 2 LIGHTSPEED", "Windows WASAPI", 2),
            DeviceCandidate(2, "CABLE Input (VB-Audio Virtual Cable)", "Windows WASAPI", 2),
            DeviceCandidate(3, "Primary Sound Driver", "Windows DirectSound", 2),
        ]

        with (
            patch.object(self.engine, "_device_candidates", return_value=candidates),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_output_devices()

        self.assertEqual(devices, [("1", "Logitech PRO X 2 LIGHTSPEED")])

    def test_rescan_reinitializes_portaudio_after_stopping_streams(self) -> None:
        with (
            patch.object(self.engine, "stop_all_sounds") as stop_all,
            patch.object(self.engine, "_stop_stream_locked") as stop_stream,
            patch("soundboard_qt6.audio_engine.sd._terminate") as terminate,
            patch("soundboard_qt6.audio_engine.sd._initialize") as initialize,
        ):
            self.engine.rescan_devices()

        stop_all.assert_called_once_with()
        stop_stream.assert_called_once_with()
        terminate.assert_called_once_with()
        initialize.assert_called_once_with()

    def test_mixer_prefers_one_synchronized_duplex_stream(self) -> None:
        self.engine._input_device_id = "101"
        self.engine._output_device_id = "202"
        device_info = {
            101: {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
            202: {"name": "Logitech Headset", "max_input_channels": 0, "max_output_channels": 2},
        }

        with (
            patch("soundboard_qt6.audio_engine.sd.query_devices", side_effect=lambda index: device_info[index]),
            patch("soundboard_qt6.audio_engine.sd.check_input_settings"),
            patch("soundboard_qt6.audio_engine.sd.check_output_settings"),
            patch("soundboard_qt6.audio_engine.sd.Stream") as duplex_stream,
            patch("soundboard_qt6.audio_engine.sd.InputStream") as input_stream,
            patch("soundboard_qt6.audio_engine.sd.OutputStream") as output_stream,
            patch.object(self.engine, "_start_virtual_output_locked", return_value="Virtual mic ready"),
        ):
            self.engine.restart_stream()

        self.assertEqual(duplex_stream.call_args.kwargs["device"], (101, 202))
        duplex_stream.return_value.start.assert_called_once_with()
        input_stream.assert_not_called()
        output_stream.assert_not_called()

    def test_mixer_falls_back_for_an_incompatible_logitech_wireless_output(self) -> None:
        self.engine._input_device_id = "101"
        self.engine._output_device_id = "202"
        device_info = {
            101: {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
            202: {"name": "Logitech Wireless Headset", "max_input_channels": 0, "max_output_channels": 2},
        }

        with (
            patch("soundboard_qt6.audio_engine.sd.query_devices", side_effect=lambda index: device_info[index]),
            patch("soundboard_qt6.audio_engine.sd.check_input_settings"),
            patch("soundboard_qt6.audio_engine.sd.check_output_settings"),
            patch("soundboard_qt6.audio_engine.sd.Stream", side_effect=RuntimeError("incompatible clocks")),
            patch("soundboard_qt6.audio_engine.sd.InputStream") as input_stream,
            patch("soundboard_qt6.audio_engine.sd.OutputStream") as output_stream,
            patch.object(self.engine, "_start_virtual_output_locked", return_value="Virtual mic ready"),
        ):
            self.engine.restart_stream()

        self.assertEqual(input_stream.call_args.kwargs["device"], 101)
        self.assertEqual(input_stream.call_args.kwargs["latency"], "high")
        self.assertEqual(output_stream.call_args.kwargs["device"], 202)
        self.assertEqual(output_stream.call_args.kwargs["latency"], "high")
        input_stream.return_value.start.assert_called_once_with()
        output_stream.return_value.start.assert_called_once_with()

    def test_device_probe_falls_back_to_mono_when_stereo_is_unsupported(self) -> None:
        candidate = DeviceCandidate(7, "USB Headset", "Windows WASAPI", 2)

        def check_input_settings(**kwargs) -> None:
            if kwargs["channels"] == 2:
                raise RuntimeError("stereo unsupported")

        with patch(
            "soundboard_qt6.audio_engine.sd.check_input_settings",
            side_effect=check_input_settings,
        ) as check:
            self.assertTrue(self.engine._device_is_usable(candidate, is_input=True))

        self.assertEqual([call.kwargs["channels"] for call in check.call_args_list], [2, 1])

    def test_real_device_with_wave_in_its_name_is_not_hidden(self) -> None:
        wave_microphone = DeviceCandidate(
            4,
            "Microphone (Elgato Wave:3)",
            "Windows WASAPI",
            2,
        )
        with (
            patch.object(self.engine, "_device_candidates", return_value=[wave_microphone]),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_input_devices()

        self.assertEqual(devices, [("4", "Microphone (Elgato Wave:3)")])

    def test_non_finite_sound_cannot_poison_the_virtual_mic_limiter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            sound_path = Path(temporary_dir) / "non-finite.wav"
            import soundfile as sf

            samples = np.zeros((1_000, 2), dtype=np.float32)
            samples[10, 0] = np.nan
            samples[20, 1] = np.inf
            sf.write(sound_path, samples, 48_000, format="WAV", subtype="FLOAT")

            prepared = self.engine._get_prepared_sound(sound_path, "Protected", emit_errors=False)
            self.assertIsNotNone(prepared)
            self.engine._virtual_limiter_gain = float("nan")
            mixed = self.engine._prepare_voice_app_mix(
                np.zeros((256, 2), dtype=np.float32),
                prepared.virtual_samples[:256],
            )

            self.assertTrue(np.isfinite(prepared.monitor_samples).all())
            self.assertTrue(np.isfinite(prepared.virtual_samples).all())
            self.assertTrue(np.isfinite(mixed).all())
            self.assertTrue(np.isfinite(self.engine._virtual_limiter_gain))

    def test_virtual_mix_uses_unity_gain_below_the_limiter_ceiling(self) -> None:
        self.engine.set_mic_noise_gate(False, -42.0)
        mic = np.full((256, 2), 0.2, dtype=np.float32)
        sound = np.full((256, 2), 0.3, dtype=np.float32)

        mixed = self.engine._prepare_voice_app_mix(mic, sound)

        np.testing.assert_allclose(mixed, 0.5, atol=1e-6)

    def test_virtual_limiter_catches_the_first_overloaded_sample(self) -> None:
        overloaded = np.array([[2.0], [-2.0], [0.25]], dtype=np.float32)

        limited = self.engine._apply_virtual_limiter(overloaded)

        self.assertLessEqual(float(np.max(np.abs(limited))), self.engine._virtual_peak_ceiling + 1e-6)
        self.assertAlmostEqual(float(limited[0, 0]), self.engine._virtual_peak_ceiling, places=6)

    def test_repeated_audio_status_is_rate_limited_without_hiding_changes(self) -> None:
        messages: list[str] = []
        self.engine.log.connect(messages.append)

        with patch("soundboard_qt6.audio_engine.time.monotonic", side_effect=[10.0, 11.0, 12.0, 16.0]):
            self.engine._report_stream_status("Virtual output status", "underflow")
            self.engine._report_stream_status("Virtual output status", "underflow")
            self.engine._report_stream_status("Virtual output status", "overflow")
            self.engine._report_stream_status("Virtual output status", "overflow")

        self.assertEqual(
            messages,
            [
                "Virtual output status: underflow",
                "Virtual output status: overflow",
            ],
        )

    def test_compatibility_input_callback_mixes_mic_without_resampling_it(self) -> None:
        mic_frames = np.linspace(-0.5, 0.5, 512, dtype=np.float32).reshape(256, 2)
        self.engine._monitor_output_stream = object()
        self.engine._reset_monitor_output_queue()

        with patch.object(self.engine, "_render_mix_block", return_value=mic_frames) as render:
            self.engine._input_callback(mic_frames, 256, None, None)
        delivered = self.engine._pop_monitor_output_frames(256, 2)

        np.testing.assert_allclose(render.call_args.args[1], mic_frames)
        np.testing.assert_allclose(delivered, mic_frames)

    def test_synchronized_callback_uses_microphone_frames_directly(self) -> None:
        mic_frames = np.linspace(-0.5, 0.5, 512, dtype=np.float32).reshape(256, 2)
        output = np.zeros_like(mic_frames)

        with patch.object(self.engine, "_render_mix_block", return_value=mic_frames) as render:
            self.engine._audio_callback(mic_frames, output, 256, None, None)

        np.testing.assert_allclose(render.call_args.args[1], mic_frames)
        np.testing.assert_allclose(output, mic_frames)

    def test_only_remote_audio_is_reported_as_active_playback(self) -> None:
        events: list[list[str]] = []
        self.engine.playback_state_changed.connect(events.append)
        prepared = PreparedSound(
            monitor_samples=np.ones((4, 2), dtype=np.float32),
            virtual_samples=np.ones((4, 2), dtype=np.float32),
            raw_virtual_samples=np.ones((4, 2), dtype=np.float32),
        )

        with tempfile.NamedTemporaryFile(suffix=".wav") as sound_file:
            pad = PadConfig(id="pad-01", label="Test", sound_path=sound_file.name)
            with patch.object(self.engine, "_get_prepared_sound", return_value=prepared):
                self.engine.trigger_pad(pad, route_to_virtual=False)
                self.assertEqual(events, [])

                self.engine.trigger_pad(pad, route_to_virtual=True)
                self.assertEqual(events[-1], ["pad-01"])

                self.engine._mix_voices(4, 2)
                self.assertEqual(events[-1], [])

    def test_stacked_playback_is_bounded_to_protect_the_audio_callback(self) -> None:
        prepared = PreparedSound(
            monitor_samples=np.ones((4, 2), dtype=np.float32),
            virtual_samples=np.ones((4, 2), dtype=np.float32),
            raw_virtual_samples=np.ones((4, 2), dtype=np.float32),
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as sound_file:
            pad = PadConfig(id="pad-01", label="Test", sound_path=sound_file.name)
            with patch.object(self.engine, "_get_prepared_sound", return_value=prepared):
                for _ in range(self.engine._max_active_voices + 10):
                    self.engine.trigger_pad(pad, log_action=False)

        self.assertEqual(len(self.engine._voices), self.engine._max_active_voices)

    def test_sound_pack_avoids_immediately_repeating_the_same_random_clip(self) -> None:
        prepared = PreparedSound(
            monitor_samples=np.ones((4, 2), dtype=np.float32),
            virtual_samples=np.ones((4, 2), dtype=np.float32),
            raw_virtual_samples=np.ones((4, 2), dtype=np.float32),
        )
        with tempfile.TemporaryDirectory() as temporary_dir:
            pack_dir = Path(temporary_dir)
            (pack_dir / "a.wav").write_bytes(b"a")
            (pack_dir / "b.wav").write_bytes(b"b")
            pad = PadConfig(
                id="pad-01",
                label="Random",
                sound_mode="pack",
                sound_pack_path=str(pack_dir),
            )
            chosen: list[str] = []

            def prepare(path, _label, *, emit_errors):
                self.assertTrue(emit_errors)
                chosen.append(path.name)
                return prepared

            with (
                patch("soundboard_qt6.audio_engine.random.shuffle", side_effect=lambda values: None),
                patch.object(self.engine, "_get_prepared_sound", side_effect=prepare),
            ):
                self.engine.trigger_pad(pad, log_action=False)
                self.engine.trigger_pad(pad, log_action=False)

            self.assertEqual(chosen, ["a.wav", "b.wav"])

    def test_manually_added_oversized_pack_clip_is_skipped_for_a_valid_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            pack_dir = Path(temporary_dir)
            import soundfile as sf

            sf.write(pack_dir / "a-too-long.wav", np.zeros(8_000 * 16, dtype=np.float32), 8_000)
            sf.write(pack_dir / "b-valid.wav", np.zeros(8_000, dtype=np.float32), 8_000)
            pad = PadConfig(
                id="pad-01",
                label="Protected pack",
                sound_mode="pack",
                sound_pack_path=str(pack_dir),
            )
            messages: list[str] = []
            self.engine.log.connect(messages.append)

            with patch("soundboard_qt6.audio_engine.random.shuffle", side_effect=lambda values: None):
                self.engine.trigger_pad(pad, route_to_virtual=False, log_action=False)

            self.assertIsNotNone(self.engine.pad_playback_position_seconds(pad.id))
            self.assertTrue(any("15 seconds or shorter" in message for message in messages))

    def test_voice_limit_keeps_remote_playing_state_in_sync(self) -> None:
        self.engine._max_active_voices = 1
        events: list[list[str]] = []
        self.engine.playback_state_changed.connect(events.append)
        prepared = PreparedSound(
            monitor_samples=np.ones((4, 2), dtype=np.float32),
            virtual_samples=np.ones((4, 2), dtype=np.float32),
            raw_virtual_samples=np.ones((4, 2), dtype=np.float32),
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as sound_file:
            remote_pad = PadConfig(id="pad-01", label="Remote", sound_path=sound_file.name)
            preview_pad = PadConfig(id="preview", label="Preview", sound_path=sound_file.name)
            with patch.object(self.engine, "_get_prepared_sound", return_value=prepared):
                self.engine.trigger_pad(remote_pad, log_action=False)
                self.engine.trigger_pad(preview_pad, route_to_virtual=False, log_action=False)

        self.assertEqual(events, [["pad-01"], []])

    def test_decoded_sound_cache_discards_old_entries(self) -> None:
        self.engine._max_cached_sounds = 2
        with tempfile.TemporaryDirectory() as temporary_dir:
            import soundfile as sf

            for index in range(3):
                sound_path = Path(temporary_dir) / f"sound-{index}.wav"
                sf.write(sound_path, np.full(80, index / 10, dtype=np.float32), 8_000)
                self.assertIsNotNone(
                    self.engine._get_prepared_sound(sound_path, sound_path.name, emit_errors=True)
                )

        self.assertEqual(len(self.engine._sound_cache), 2)

    def test_preview_position_comes_from_the_active_audio_voice(self) -> None:
        prepared = PreparedSound(
            monitor_samples=np.ones((48_000, 2), dtype=np.float32),
            virtual_samples=np.zeros((48_000, 2), dtype=np.float32),
            raw_virtual_samples=np.zeros((48_000, 2), dtype=np.float32),
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as sound_file:
            pad = PadConfig(id="clip-preview", label="Preview", sound_path=sound_file.name)
            with patch.object(self.engine, "_get_prepared_sound", return_value=prepared):
                self.engine.trigger_pad(pad, route_to_virtual=False, log_action=False)
                self.assertEqual(self.engine.pad_playback_position_seconds(pad.id), 0.0)

                self.engine._mix_voices(12_000, 2)
                self.assertAlmostEqual(self.engine.pad_playback_position_seconds(pad.id), 0.25)

                self.engine._mix_voices(36_000, 2)
                self.assertIsNone(self.engine.pad_playback_position_seconds(pad.id))

    def test_audio_readiness_changes_only_when_route_state_changes(self) -> None:
        events: list[bool] = []
        self.engine.readiness_changed.connect(events.append)

        self.engine._set_audio_ready(True)
        self.engine._set_audio_ready(True)
        self.engine._set_audio_ready(False)

        self.assertEqual(events, [True, False])

    def test_running_mixer_requires_a_live_vb_cable_output(self) -> None:
        events: list[bool] = []
        self.engine.readiness_changed.connect(events.append)
        self.engine._duplex_stream = object()

        self.engine._update_audio_ready_locked()
        self.assertFalse(self.engine.audio_ready())

        self.engine._virtual_output_stream = object()
        self.engine._update_audio_ready_locked()
        self.assertTrue(self.engine.audio_ready())

        self.engine._virtual_output_stream = None
        self.engine._update_audio_ready_locked()
        self.assertEqual(events, [True, False])

    def test_virtual_cable_queue_preserves_samples_without_pitch_correction(self) -> None:
        self.engine._virtual_output_stream = object()
        self.engine._virtual_output_channels = 2
        self.engine._reset_virtual_output_queue()
        self.engine._push_virtual_output_frames(np.ones((3_000, 2), dtype=np.float32))

        output = self.engine._pop_virtual_output_frames(512, 2)

        self.assertEqual(output.shape, (512, 2))
        self.assertEqual(self.engine._virtual_buffer_frames, 3_000 - 512)

    def test_virtual_cable_prefers_low_latency_quality_profile(self) -> None:
        with (
            patch.object(self.engine, "_find_virtual_cable_output_index", return_value=18),
            patch(
                "soundboard_qt6.audio_engine.sd.query_devices",
                return_value={"name": "CABLE Input", "max_output_channels": 2},
            ),
            patch("soundboard_qt6.audio_engine.sd.OutputStream") as output_stream,
        ):
            status = self.engine._start_virtual_output_locked(excluded_output_index=20)

        self.assertEqual(output_stream.call_args.kwargs["blocksize"], 256)
        self.assertEqual(output_stream.call_args.kwargs["latency"], 0.02)
        self.assertEqual(self.engine._virtual_buffer_frames, 512)
        self.assertEqual(status, "Virtual mic active: CABLE Input")
        output_stream.return_value.start.assert_called_once_with()

    def test_virtual_cable_uses_quality_fallback_when_low_latency_start_fails(self) -> None:
        low_latency_stream = MagicMock()
        low_latency_stream.start.side_effect = RuntimeError("low latency unavailable")
        fallback_stream = MagicMock()

        with (
            patch.object(self.engine, "_find_virtual_cable_output_index", return_value=18),
            patch(
                "soundboard_qt6.audio_engine.sd.query_devices",
                return_value={"name": "CABLE Input", "max_output_channels": 2},
            ),
            patch(
                "soundboard_qt6.audio_engine.sd.OutputStream",
                side_effect=[low_latency_stream, fallback_stream],
            ) as output_stream,
        ):
            status = self.engine._start_virtual_output_locked(excluded_output_index=20)

        self.assertEqual(output_stream.call_count, 2)
        self.assertEqual(output_stream.call_args_list[0].kwargs["blocksize"], 256)
        self.assertEqual(output_stream.call_args_list[0].kwargs["latency"], 0.02)
        self.assertEqual(output_stream.call_args_list[1].kwargs["blocksize"], 512)
        self.assertEqual(output_stream.call_args_list[1].kwargs["latency"], 0.06)
        self.assertEqual(self.engine._virtual_buffer_frames, 2_048)
        self.assertIn("compatibility buffering", status)
        low_latency_stream.close.assert_called_once_with()
        fallback_stream.start.assert_called_once_with()

    def test_real_line_input_is_not_hidden_from_the_microphone_list(self) -> None:
        line_input = DeviceCandidate(7, "Line In (USB Audio)", "Windows WASAPI", 2)
        with (
            patch.object(self.engine, "_device_candidates", return_value=[line_input]),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_input_devices()

        self.assertEqual(devices, [("7", "Line In (USB Audio)")])

    def test_fresh_device_list_prefers_a_microphone_over_line_input(self) -> None:
        candidates = [
            DeviceCandidate(7, "Line In (USB Audio)", "Windows WASAPI", 2),
            DeviceCandidate(8, "Microphone (USB Audio)", "Windows WASAPI", 1),
        ]
        with (
            patch.object(self.engine, "_device_candidates", return_value=candidates),
            patch.object(self.engine, "_device_is_usable", return_value=True),
        ):
            devices = self.engine.available_input_devices()

        self.assertEqual(devices[0], ("8", "Microphone (USB Audio)"))

    def test_manual_same_name_replacement_with_oversized_audio_cannot_play(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            sound_path = Path(temporary_dir) / "managed-pad.wav"
            import soundfile as sf

            sf.write(sound_path, np.zeros(8_000, dtype=np.float32), 8_000)
            pad = PadConfig(id="pad-01", label="Protected", sound_path=str(sound_path))
            self.engine.trigger_pad(pad, route_to_virtual=False, log_action=False)
            self.assertIsNotNone(self.engine.pad_playback_position_seconds(pad.id))
            self.engine.stop_all_sounds()

            sf.write(sound_path, np.zeros(8_000 * 16, dtype=np.float32), 8_000)
            messages: list[str] = []
            self.engine.log.connect(messages.append)
            self.engine.trigger_pad(pad, route_to_virtual=True, log_action=False)

            self.assertIsNone(self.engine.pad_playback_position_seconds(pad.id))
            self.assertTrue(any("15 seconds or shorter" in message for message in messages))

    def test_decoded_duration_is_rechecked_after_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            sound_path = Path(temporary_dir) / "changed-during-load.wav"
            import soundfile as sf

            sf.write(sound_path, np.zeros(8_000 * 16, dtype=np.float32), 8_000)
            with patch("soundboard_qt6.audio_engine.validate_sound_duration", return_value=1.0):
                prepared = self.engine._get_prepared_sound(sound_path, "Protected", emit_errors=False)

            self.assertIsNone(prepared)


class RemoteServerTests(unittest.TestCase):
    def test_starting_status_is_emitted_before_a_fast_worker_result(self) -> None:
        server = BrowserRemoteServer(Path("."), 8765, lambda: {}, lambda _pad_id: None)
        events: list[tuple[str, str]] = []
        server.status_changed.connect(lambda status, detail: events.append((status, detail)))

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                del daemon
                self._target = target

            def start(self):
                self._target()

            @staticmethod
            def is_alive():
                return False

        with (
            patch("soundboard_qt6.remote_server.threading.Thread", ImmediateThread),
            patch.object(server, "_run", side_effect=lambda: server._set_status("failed", "fast failure")),
        ):
            started = server.start()

        self.assertTrue(started)
        self.assertEqual(events, [("starting", ""), ("failed", "fast failure")])

    def test_local_websocket_rejects_cross_site_browser_origins(self) -> None:
        self.assertTrue(
            BrowserRemoteServer._origin_matches_host(
                "http://192.168.50.23:8765",
                "192.168.50.23:8765",
            )
        )
        self.assertFalse(
            BrowserRemoteServer._origin_matches_host(
                "https://malicious.example",
                "127.0.0.1:8765",
            )
        )
        self.assertFalse(
            BrowserRemoteServer._origin_matches_host(
                "http://127.0.0.1:9999",
                "127.0.0.1:8765",
            )
        )

    def test_websocket_origin_rules_cover_internet_and_dns_rebinding(self) -> None:
        allowed = BrowserRemoteServer._websocket_request_allowed

        self.assertTrue(
            allowed(
                "private-token",
                "https://quiet-river.trycloudflare.com",
                "quiet-river.trycloudflare.com",
            )
        )
        self.assertFalse(
            allowed(
                "private-token",
                "https://malicious.example",
                "quiet-river.trycloudflare.com",
            )
        )
        self.assertTrue(allowed("", "http://192.168.50.23:8765", "192.168.50.23:8765"))
        self.assertFalse(allowed("", "http://rebind.example:8765", "rebind.example:8765"))

    def test_malformed_client_messages_are_ignored_without_disconnects(self) -> None:
        decode = BrowserRemoteServer._decode_client_message

        self.assertIsNone(decode("[]"))
        self.assertIsNone(decode("null"))
        self.assertIsNone(decode("not json"))
        self.assertEqual(decode('{"type":"stop_all","payload":[]}'), ("stop_all", {}))
        self.assertEqual(
            decode('{"type":"trigger_pad","payload":{"padId":"pad-01"}}'),
            ("trigger_pad", {"padId": "pad-01"}),
        )

    def test_state_provider_failure_returns_safe_empty_state(self) -> None:
        def fail_state() -> dict:
            raise RuntimeError("state changed")

        server = BrowserRemoteServer(Path("."), 8765, fail_state, lambda _pad_id: None)
        messages: list[str] = []
        server.log.connect(messages.append)

        self.assertEqual(server._state_snapshot(), {})
        self.assertIn("Could not refresh", messages[-1])

    def test_primary_lan_address_is_used_for_the_qr_url(self) -> None:
        server = BrowserRemoteServer(Path("."), 8765, lambda: {}, lambda _pad_id: None)
        with (
            patch.object(server, "_candidate_ipv4_addresses", return_value=["10.0.0.9", "192.168.50.23"]),
            patch.object(server, "_primary_ipv4_address", return_value="192.168.50.23"),
        ):
            self.assertEqual(server.primary_page_url(), "http://192.168.50.23:8765/")

    def test_link_local_addresses_are_not_exposed(self) -> None:
        self.assertFalse(BrowserRemoteServer._is_remote_address("169.254.12.34"))
        self.assertTrue(BrowserRemoteServer._is_remote_address("192.168.50.23"))
        self.assertTrue(BrowserRemoteServer._is_remote_address("100.64.12.34"))
        self.assertFalse(BrowserRemoteServer._is_remote_address("8.8.8.8"))

    def test_local_websocket_host_must_be_a_local_network_address(self) -> None:
        self.assertTrue(BrowserRemoteServer._is_literal_local_host("127.0.0.1:8765"))
        self.assertTrue(BrowserRemoteServer._is_literal_local_host("192.168.50.23:8765"))
        self.assertTrue(BrowserRemoteServer._is_literal_local_host("100.64.12.34:8765"))
        self.assertFalse(BrowserRemoteServer._is_literal_local_host("8.8.8.8:8765"))

    def test_versioned_pad_images_are_cacheable(self) -> None:
        headers = BrowserRemoteServer._versioned_asset_headers()
        self.assertIn("immutable", headers["Cache-Control"])

    def test_stop_during_startup_does_not_leave_an_orphan_server_thread(self) -> None:
        server = BrowserRemoteServer(Path("."), 8765, lambda: {}, lambda _pad_id: None)
        startup_entered = threading.Event()

        async def delayed_start() -> None:
            startup_entered.set()
            while not server._stop_requested.is_set():
                import asyncio

                await asyncio.sleep(0.005)

        server._start_site = delayed_start
        server.start()
        self.assertTrue(startup_entered.wait(timeout=1))
        first_thread = server._thread

        server.stop()

        self.assertIsNotNone(first_thread)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(server.status, "stopped")


class RemoteAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = BrowserRemoteServer(Path("."), 8765, lambda: {}, lambda _pad_id: None)
        self.server.set_access_token("private-session-key")
        app = web.Application(middlewares=[self.server._authentication_middleware])

        async def remote_index(_request):
            return web.Response(text="remote ready")

        app.router.add_get("/", remote_index)
        app.router.add_get("/connect/{access_token}", remote_index)
        self.client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_internet_remote_requires_qr_token_then_uses_private_cookie(self) -> None:
        unauthorized = await self.client.get("/", allow_redirects=False)
        self.assertEqual(unauthorized.status, 401)

        accepted = await self.client.get("/connect/private-session-key", allow_redirects=False)
        self.assertEqual(accepted.status, 302)
        cookie_header = accepted.headers.get("Set-Cookie", "")
        self.assertIn("soundboard_remote_access", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)

        authenticated = await self.client.get("/")
        self.assertEqual(authenticated.status, 200)
        self.assertEqual(await authenticated.text(), "remote ready")

        self.server.set_access_token("rotated-key")
        expired = await self.client.get("/", allow_redirects=False)
        self.assertEqual(expired.status, 401)

    async def test_legacy_query_link_still_authenticates(self) -> None:
        accepted = await self.client.get("/?access=private-session-key", allow_redirects=False)

        self.assertEqual(accepted.status, 302)
        self.assertIn("SameSite=Lax", accepted.headers.get("Set-Cookie", ""))


class GlobalHotkeyTests(unittest.TestCase):
    def test_modifier_chords_are_normalized_and_mapped_for_windows(self) -> None:
        self.assertEqual(normalize_hotkey("Ctrl+Shift+1"), "Ctrl+Shift+1")
        self.assertEqual(parse_hotkey("Ctrl+Shift+1"), (MOD_CONTROL | MOD_SHIFT, ord("1")))
        self.assertEqual(parse_hotkey("Ctrl+Alt+4"), (MOD_CONTROL | MOD_ALT, ord("4")))
        self.assertEqual(parse_hotkey("Ctrl+Alt+Num+4"), (MOD_CONTROL | MOD_ALT, 0x64))
        self.assertEqual(describe_hotkey_key_source("Ctrl+Alt+4"), "Top-row 4")
        self.assertEqual(describe_hotkey_key_source("Ctrl+Alt+Num+4"), "Numpad 4")
        self.assertEqual(parse_hotkey("Alt+F8"), (MOD_ALT, 0x77))

    def test_multi_step_shortcut_is_rejected_in_favor_of_one_chord(self) -> None:
        with self.assertRaisesRegex(HotkeyError, "one shortcut combination"):
            normalize_hotkey("Ctrl+K, Ctrl+C")

    def test_quick_tunnel_url_is_extracted_without_accepting_other_hosts(self) -> None:
        output = "INF Your quick Tunnel has been created! Visit https://calm-wave.trycloudflare.com"
        self.assertEqual(extract_public_url(output), "https://calm-wave.trycloudflare.com")
        self.assertEqual(extract_public_url("https://example.com/not-a-tunnel"), "")

    def test_unsigned_internet_helper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            helper = Path(temporary_dir) / "cloudflared.exe"
            helper.write_bytes(b"MZ" + bytes(5 * 1024 * 1024))
            with patch("soundboard_qt6.internet_tunnel.is_authenticode_signature_valid", return_value=False):
                self.assertFalse(InternetTunnel._valid_helper(helper))

    def test_tunnel_starting_status_precedes_a_fast_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            tunnel = InternetTunnel(Path(temporary_dir))
            events: list[tuple[str, str]] = []
            tunnel.status_changed.connect(lambda status, detail: events.append((status, detail)))

            class ImmediateThread:
                def __init__(self, *, target, args, daemon):
                    del daemon
                    self._target = target
                    self._args = args

                def start(self):
                    self._target(*self._args)

                @staticmethod
                def is_alive():
                    return False

            def fail_fast(generation, _port):
                tunnel._set_failed(generation, "fast failure")

            with (
                patch("soundboard_qt6.internet_tunnel.threading.Thread", ImmediateThread),
                patch.object(tunnel, "_run", side_effect=fail_fast),
            ):
                tunnel.start(8765)

            self.assertEqual(events, [("starting", ""), ("failed", "fast failure")])


class SingleInstanceTests(unittest.TestCase):
    def test_second_guard_for_same_executable_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            identity = Path(temporary_dir) / "SoundboardQt6.exe"
            first = SingleInstanceGuard(identity)
            second = SingleInstanceGuard(identity)
            try:
                self.assertTrue(first.acquired)
                self.assertFalse(second.acquired)
            finally:
                second.close()
                first.close()

    def test_second_copy_in_another_folder_is_also_rejected(self) -> None:
        first = SingleInstanceGuard(Path("C:/First/SoundboardQt6.exe"))
        second = SingleInstanceGuard(Path("D:/Portable/SoundboardQt6.exe"))
        try:
            self.assertTrue(first.acquired)
            self.assertFalse(second.acquired)
        finally:
            second.close()
            first.close()


class MainWindowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_cleanup_keeps_a_pack_directory_that_contains_a_referenced_sound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")
            pack = store.sounds_dir / "pad-06" / "pack-existing"
            pack.mkdir(parents=True)
            referenced_sound = pack / "shared.wav"
            referenced_sound.write_bytes(b"still referenced")

            class AssetHost:
                asset_store = store

                @staticmethod
                def _referenced_runtime_assets() -> set[Path]:
                    return {referenced_sound.resolve()}

            MainWindow._cleanup_unused_runtime_assets(AssetHost(), [pack])

            self.assertTrue(pack.is_dir())
            self.assertTrue(referenced_sound.is_file())

    def test_moved_runtime_asset_is_recovered_beside_the_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            current_dir = Path(temporary_dir) / "runtime" / "sounds"
            current_dir.mkdir(parents=True)
            current_asset = current_dir / "pad-01-effect-123.wav"
            current_asset.write_bytes(b"sound")
            old_path = Path("D:/Old Soundboard/runtime/sounds") / current_asset.name

            recovered = MainWindow._recover_moved_runtime_asset(str(old_path), current_dir)

            self.assertEqual(Path(recovered), current_asset.resolve())

    def test_moved_runtime_sound_pack_is_recovered_beside_the_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            current_dir = Path(temporary_dir) / "runtime" / "sounds"
            current_pack = current_dir / "pad-03" / "pack-abc123"
            current_pack.mkdir(parents=True)
            (current_pack / "sound.wav").write_bytes(b"sound")
            old_path = Path("D:/Old Soundboard/runtime/sounds/pad-03/pack-abc123")

            recovered = MainWindow._recover_moved_runtime_asset(str(old_path), current_dir)

            self.assertEqual(Path(recovered), current_pack.resolve())

    def test_sound_pack_mode_copies_folder_saves_and_previews_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_pack = root / "source-pack"
            source_pack.mkdir()
            import soundfile as sf

            sf.write(source_pack / "first.wav", np.zeros(8_000, dtype=np.float32), 8_000)
            sf.write(source_pack / "second.wav", np.zeros(8_000, dtype=np.float32), 8_000)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1] / "src" / "python_qt6" / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.refresh_editor()
                window.sound_mode_combo.setCurrentIndex(window.sound_mode_combo.findData("pack"))
                with patch.object(window, "_show_sound_validation_warning") as warning:
                    with patch(
                        "soundboard_qt6.main_window.QFileDialog.getExistingDirectory",
                        return_value=str(source_pack),
                    ):
                        window.browse_sound_pack_folder()
                warning.assert_not_called()

                managed_pack = Path(window.sound_edit.text())
                self.assertEqual(managed_pack.parent.name, "pad-01")
                self.assertEqual(len(sound_pack_files(managed_pack)), 2)
                self.assertEqual(window.sound_summary_label.text(), "2 sounds, random")
                self.assertEqual(window.sound_browse_button.text(), "Folder")
                self.assertTrue(window.sound_edit_button.isHidden())

                with patch.object(window.audio_engine, "preload_pads"):
                    window.save_selected_pad()

                self.assertEqual(window.config.pads[0].sound_mode, "pack")
                self.assertEqual(Path(window.config.pads[0].sound_pack_path), managed_pack)
                saved = json.loads(paths.config_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["pads"][0]["sound_mode"], "pack")
                self.assertEqual(saved["pads"][0]["sound_pack_path"], str(managed_pack))

                with patch.object(window.audio_engine, "trigger_pad") as trigger:
                    window.play_selected_pad()
                preview_pad = trigger.call_args.args[0]
                self.assertEqual(preview_pad.sound_mode, "pack")
                self.assertEqual(preview_pad.sound_pack_path, str(managed_pack))
                self.assertFalse(trigger.call_args.kwargs["route_to_virtual"])
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_sound_pack_folder_skips_long_clips_and_names_them_in_one_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_pack = root / "source-pack"
            source_pack.mkdir()
            import soundfile as sf

            sf.write(source_pack / "valid.wav", np.zeros(8_000), 8_000)
            sf.write(source_pack / "too-long.wav", np.zeros(8_000 * 16), 8_000)
            rejected_pack = root / "rejected-pack"
            rejected_pack.mkdir()
            sf.write(rejected_pack / "only-long.wav", np.zeros(8_000 * 17), 8_000)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.refresh_editor()
                window.sound_mode_combo.setCurrentIndex(window.sound_mode_combo.findData("pack"))
                with patch.object(window, "_show_sound_validation_warning") as warning:
                    with patch(
                        "soundboard_qt6.main_window.QFileDialog.getExistingDirectory",
                        return_value=str(source_pack),
                    ):
                        window.browse_sound_pack_folder()

                managed_pack = Path(window.sound_edit.text())
                self.assertEqual(len(sound_pack_files(managed_pack)), 1)
                self.assertEqual(window.sound_summary_label.text(), "1 sound, random")
                warning.assert_called_once()
                message, title = warning.call_args.args
                self.assertEqual(title, "Some sounds were skipped")
                self.assertIn("too-long.wav", message)
                self.assertNotIn("- valid.wav", message)

                previous_pack = window.sound_edit.text()
                with patch.object(window, "_show_sound_validation_warning") as rejected_warning:
                    with patch(
                        "soundboard_qt6.main_window.QFileDialog.getExistingDirectory",
                        return_value=str(rejected_pack),
                    ):
                        window.browse_sound_pack_folder()
                self.assertEqual(window.sound_edit.text(), previous_pack)
                rejected_warning.assert_called_once()
                rejected_message = rejected_warning.call_args.args[0]
                self.assertIn("Sound pack not added", rejected_message)
                self.assertIn("only-long.wav", rejected_message)
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_profile_zip_round_trip_preserves_every_sound_pack_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_pack = root / "source-pack"
            source_pack.mkdir()
            import soundfile as sf

            sf.write(source_pack / "one.wav", np.zeros(8_000, dtype=np.float32), 8_000)
            sf.write(source_pack / "two.wav", np.ones(8_000, dtype=np.float32) * 0.1, 8_000)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1] / "src" / "python_qt6" / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                files = validate_sound_pack(source_pack)
                managed_pack = window.asset_store.copy_sound_pack(str(source_pack), "pad-01", files)
                window.config.pads[0].sound_mode = "pack"
                window.config.pads[0].sound_pack_path = str(managed_pack)
                profile_path = root / "profile.zip"

                window._write_profile_zip(profile_path)
                imported_pads, _settings = window._read_profile_zip(profile_path)

                with zipfile.ZipFile(profile_path) as archive:
                    payload = json.loads(archive.read("soundboard_profile.json"))
                    pack_entries = payload["pads"][0]["sound_pack_paths"]
                    self.assertEqual(len(pack_entries), 2)
                    self.assertTrue(all(entry in archive.namelist() for entry in pack_entries))
                self.assertEqual(imported_pads[0].sound_mode, "pack")
                self.assertEqual(len(sound_pack_files(imported_pads[0].sound_pack_path)), 2)
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_legacy_12_pad_profile_import_adds_blank_new_slots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            profile_path = root / "legacy-profile.zip"
            payload = {
                "profile_version": 3,
                "pads": [
                    {
                        "id": f"pad-{index:02d}",
                        "label": f"Legacy {index}",
                        "color_hex": "#FF7043",
                        "volume_percent": 100,
                    }
                    for index in range(1, 13)
                ],
            }
            with zipfile.ZipFile(profile_path, "w") as archive:
                archive.writestr("soundboard_profile.json", json.dumps(payload))

            window = MainWindow(paths)
            try:
                imported_pads, _settings = window._read_profile_zip(profile_path)

                self.assertEqual(len(imported_pads), PAD_COUNT)
                self.assertEqual(imported_pads[0].label, "Legacy 1")
                self.assertEqual(imported_pads[11].label, "Legacy 12")
                self.assertEqual(imported_pads[12].id, "pad-13")
                self.assertEqual(imported_pads[12].label, "Pad 13")
                self.assertEqual(imported_pads[-1].id, "pad-24")
                self.assertTrue(all(not pad.sound_path for pad in imported_pads[12:]))
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_failed_profile_export_preserves_existing_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            target = root / "profile.zip"
            source = root / "sound.wav"
            target.write_bytes(b"known-good-profile")
            source.write_bytes(b"sound")

            class ProfileSource:
                @staticmethod
                def _profile_payload_and_assets():
                    return {"pads": []}, [(source, "sounds/sound.wav")]

            with patch("soundboard_qt6.main_window.zipfile.ZipFile.write", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    MainWindow._write_profile_zip(ProfileSource(), target)

            self.assertEqual(target.read_bytes(), b"known-good-profile")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_profile_export_rejects_manually_replaced_oversized_sound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            sound_path = Path(temporary_dir) / "managed-pad.wav"
            import soundfile as sf

            sf.write(sound_path, np.zeros(8_000 * 16, dtype=np.float32), 8_000)

            class ProfileSource:
                config = AppConfig(
                    pads=[PadConfig(id="pad-01", label="Protected", sound_path=str(sound_path))]
                )
                _unique_archive_asset_name = staticmethod(MainWindow._unique_archive_asset_name)

            with self.assertRaisesRegex(SoundValidationError, "15 seconds or shorter"):
                MainWindow._profile_payload_and_assets(ProfileSource())

    def test_failed_profile_import_save_keeps_the_current_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1] / "src" / "python_qt6" / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            current_config = window.config
            imported_pads = default_pads()
            imported_pads[0].label = "Imported"
            try:
                with (
                    patch(
                        "soundboard_qt6.main_window.QFileDialog.getOpenFileName",
                        return_value=(str(root / "profile.zip"), "Profile ZIP (*.zip)"),
                    ),
                    patch.object(window, "_read_profile_zip", return_value=(imported_pads, {})),
                    patch.object(window.config_manager, "save", side_effect=OSError("disk full")),
                ):
                    window.import_profile_zip()

                self.assertIs(window.config, current_config)
                self.assertNotEqual(window.config.pads[0].label, "Imported")
                self.assertIn("Could not import profile: disk full", window.log_box.toPlainText())
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_committed_profile_keeps_imported_assets_when_live_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            imported_sound = paths.sounds_dir / "pad-01-imported.wav"

            def stage_profile(_profile_path):
                import soundfile as sf

                sf.write(imported_sound, np.zeros(8_000, dtype=np.float32), 8_000)
                pads = default_pads()
                pads[0].label = "Imported"
                pads[0].sound_path = str(imported_sound)
                return pads, {}

            try:
                with (
                    patch(
                        "soundboard_qt6.main_window.QFileDialog.getOpenFileName",
                        return_value=(str(root / "profile.zip"), "Profile ZIP (*.zip)"),
                    ),
                    patch.object(window, "_read_profile_zip", side_effect=stage_profile),
                    patch.object(window, "refresh_pad_buttons", side_effect=RuntimeError("display refresh failed")),
                    patch.object(window.audio_engine, "preload_pads"),
                ):
                    window.import_profile_zip()

                saved = json.loads(paths.config_path.read_text(encoding="utf-8"))
                self.assertEqual(window.config.pads[0].label, "Imported")
                self.assertEqual(saved["pads"][0]["sound_path"], str(imported_sound))
                self.assertTrue(imported_sound.is_file())
                self.assertIn("Some live controls need an app restart", window.log_box.toPlainText())
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_virtual_mic_setup_cannot_launch_twice_and_becomes_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                with (
                    patch.object(window, "_prepare_signed_cable_package", return_value=root / "setup.exe"),
                    patch.object(window, "_write_signed_cable_install_script", return_value=root / "install.ps1"),
                    patch.object(window, "_launch_hidden_elevated_powershell") as launch,
                    patch("soundboard_qt6.main_window.QTimer.singleShot") as single_shot,
                ):
                    self.assertTrue(window.install_signed_cable(auto=False))
                    self.assertFalse(window.install_signed_cable(auto=False))

                launch.assert_called_once_with(root / "install.ps1")
                single_shot.assert_called_once()
                self.assertTrue(window._auto_cable_install_started)
                self.assertFalse(window.install_cable_button.isEnabled())

                with (
                    patch.object(window.audio_engine, "rescan_devices"),
                    patch.object(window, "refresh_devices"),
                    patch.object(window, "update_driver_status", return_value=False),
                ):
                    window._finish_virtual_mic_setup_check()

                self.assertFalse(window._auto_cable_install_started)
                self.assertIn("try again", window.log_box.toPlainText())
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_damaged_extracted_virtual_mic_installer_is_repaired_from_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            package_dir = root / "driver" / "signed_cable_fallback"
            extract_dir = package_dir / "VBCABLE_Driver_Pack45"
            setup_path = extract_dir / "VBCABLE_Setup_x64.exe"
            zip_path = package_dir / "VBCABLE_Driver_Pack45.zip"
            extract_dir.mkdir(parents=True)
            setup_path.write_bytes(b"damaged")
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("VBCABLE_Setup_x64.exe", b"signed-installer")

            window = MainWindow(paths)
            try:
                with patch(
                    "soundboard_qt6.main_window.is_authenticode_signature_valid",
                    side_effect=lambda candidate: Path(candidate).read_bytes() == b"signed-installer",
                ):
                    repaired = window._prepare_signed_cable_package()

                self.assertEqual(repaired, setup_path)
                self.assertEqual(setup_path.read_bytes(), b"signed-installer")
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_failed_pad_save_restores_the_in_memory_pad(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            original_label = window.config.pads[0].label
            try:
                window.refresh_editor()
                window.refresh_pad_buttons()
                window.label_edit.setText("Unsaved change")
                with patch.object(window.config_manager, "save", side_effect=OSError("disk full")):
                    window.save_selected_pad()

                self.assertEqual(window.config.pads[0].label, original_label)
                self.assertEqual(window.label_edit.text(), original_label)
                self.assertIn("Could not save pad-01: disk full", window.log_box.toPlainText())
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_audio_device_name_survives_portaudio_index_changes(self) -> None:
        combo = QComboBox()
        combo.addItem("HDMI Display", "19")
        combo.addItem("Headphones", "5")

        restored = MainWindow._select_combo_device(combo, "20", "Headphones")

        self.assertTrue(restored)
        self.assertEqual(combo.currentData(), "5")

    def test_missing_audio_device_is_reported_without_changing_the_fallback(self) -> None:
        combo = QComboBox()
        combo.addItem("Built-in Speakers", "1")

        restored = MainWindow._select_combo_device(combo, "20", "Logitech Headset")

        self.assertFalse(restored)
        self.assertEqual(combo.currentData(), "1")

    def test_changing_microphone_preserves_a_disconnected_headset_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.input_combo.blockSignals(True)
                window.output_combo.blockSignals(True)
                window.input_combo.clear()
                window.output_combo.clear()
                window.input_combo.addItem("Built-in Mic", "1")
                window.input_combo.addItem("USB Mic", "2")
                window.output_combo.addItem("Built-in Speakers", "9")
                window.input_combo.setCurrentIndex(0)
                window.output_combo.setCurrentIndex(0)
                window.input_combo.blockSignals(False)
                window.output_combo.blockSignals(False)
                window.config.selected_input_device_id = "1"
                window.config.selected_input_device_name = "Built-in Mic"
                window.config.selected_output_device_id = "44"
                window.config.selected_output_device_name = "Disconnected Logitech Headset"
                window._preferred_input_missing = False
                window._preferred_output_missing = True

                with (
                    patch.object(window.audio_engine, "set_devices") as set_devices,
                    patch.object(window.config_manager, "save"),
                ):
                    window.input_combo.setCurrentIndex(1)

                self.assertEqual(window.config.selected_input_device_id, "2")
                self.assertEqual(
                    window.config.selected_output_device_name,
                    "Disconnected Logitech Headset",
                )
                set_devices.assert_called_once_with("2", "9")
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_invalid_button_image_is_not_copied_into_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            invalid_image = root / "not-an-image.png"
            invalid_image.write_text("not image data", encoding="utf-8")

            class AssetHost:
                asset_store = RuntimeAssetStore(root / "runtime" / "sounds", root / "runtime" / "images")

                def __init__(self) -> None:
                    self.messages: list[str] = []

                def log(self, message: str) -> None:
                    self.messages.append(message)

            host = AssetHost()
            result = MainWindow._copy_asset_to_runtime(host, str(invalid_image), "pad-01", "image", True)

            self.assertIsNone(result)
            self.assertEqual(host.asset_store.managed_asset_files(), [])
            self.assertIn("Could not read this button image", host.messages[-1])

    def test_compact_window_keeps_editor_and_device_controls_usable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1] / "src" / "python_qt6" / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                window.input_combo.addItem("Microphone (Example Device)", "1")
                window.output_combo.addItem("Headphones (Example Device)", "2")
                window.refresh_editor()
                window.refresh_pad_buttons()
                window.startup_status_label.hide()
                window.startup_progress.hide()
                window.show()
                window.resize(1024, 680)
                QApplication.processEvents()

                self.assertLessEqual(window.minimumSizeHint().width(), 1024)
                self.assertLessEqual(window.minimumSizeHint().height(), 680)
                self.assertEqual(len(window.pad_buttons), PAD_COUNT)
                last_pad_position = window.pad_grid.getItemPosition(
                    window.pad_grid.indexOf(window.pad_buttons["pad-24"])
                )
                self.assertEqual(last_pad_position[:2], (3, 5))
                pad_widths = [button.width() for button in window.pad_buttons.values()]
                pad_heights = [button.height() for button in window.pad_buttons.values()]
                self.assertLessEqual(max(pad_widths) - min(pad_widths), 1)
                self.assertLessEqual(max(pad_heights) - min(pad_heights), 1)
                self.assertGreaterEqual(
                    window.title_label.height(),
                    window.title_label.fontMetrics().height() + 4,
                )
                for heading in window.findChildren(QLabel, "SectionTitle"):
                    self.assertGreaterEqual(heading.height(), heading.fontMetrics().height() + 2)
                self.assertGreaterEqual(window.input_combo.height(), 32)
                self.assertGreaterEqual(window.output_combo.height(), 32)
                self.assertGreaterEqual(window.sound_edit.height(), 28)
                self.assertGreaterEqual(window.image_edit.width(), 72)
                self.assertEqual(window.activity_card.height(), 80)
                self.assertEqual(window.log_box.height(), 56)
                window.hotkey_edit.setKeySequence(QKeySequence("Ctrl+Shift+1"))
                window.clear_hotkey_button.click()
                self.assertTrue(window.hotkey_edit.keySequence().isEmpty())
                window.config.pads[0].hotkey = "Ctrl+Shift+1"
                profile_payload, _assets = window._profile_payload_and_assets()
                self.assertEqual(profile_payload["pads"][0]["hotkey"], "Ctrl+Shift+1")
                qr_pixmap = window._qr_pixmap("http://192.168.1.10:8765/")
                self.assertEqual(qr_pixmap.size(), window.qr_label.size())
                for button in (
                    window.sound_browse_button,
                    window.sound_edit_button,
                    window.image_browse_button,
                ):
                    self.assertGreaterEqual(button.height(), 30)
                for button in (
                    window.test_button,
                    window.save_button,
                    window.export_profile_button,
                    window.import_profile_button,
                ):
                    self.assertGreaterEqual(button.height(), 32)

                def window_rect(widget: QWidget) -> QRect:
                    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())

                editor_controls = (
                    window.pad_selector,
                    window.label_edit,
                    window.color_edit,
                    window.volume_slider,
                    window.volume_value_label,
                    window.hotkey_edit,
                    window.clear_hotkey_button,
                    window.sound_mode_combo,
                    window.sound_edit,
                    window.sound_summary_label,
                    window.sound_browse_button,
                    window.sound_edit_button,
                    window.image_edit,
                    window.image_browse_button,
                    window.test_button,
                    window.save_button,
                    window.export_profile_button,
                    window.import_profile_button,
                )
                def assert_editor_controls_do_not_overlap() -> None:
                    visible_controls = [control for control in editor_controls if control.isVisible()]
                    for index, first in enumerate(visible_controls):
                        for second in visible_controls[index + 1 :]:
                            self.assertFalse(
                                window_rect(first).intersects(window_rect(second)),
                                f"{first.__class__.__name__} overlaps {second.__class__.__name__}",
                            )

                assert_editor_controls_do_not_overlap()
                window.sound_mode_combo.setCurrentIndex(window.sound_mode_combo.findData("pack"))
                QApplication.processEvents()
                self.assertLessEqual(window.minimumSizeHint().height(), 680)
                self.assertTrue(window.sound_summary_label.isVisible())
                self.assertFalse(window.sound_edit_button.isVisible())
                assert_editor_controls_do_not_overlap()
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_desktop_selection_preview_and_remote_buttons_keep_their_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1] / "src" / "python_qt6" / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.refresh_editor()
                window.refresh_pad_buttons()
                with patch.object(window.audio_engine, "trigger_pad") as trigger:
                    window.pad_buttons["pad-02"].click()
                    self.assertEqual(window.selected_pad_id, "pad-02")
                    trigger.assert_not_called()

                    window.play_selected_pad()
                    trigger.assert_called_once()
                    self.assertFalse(trigger.call_args.kwargs["route_to_virtual"])

                with patch.object(window.audio_engine, "trigger_pad") as trigger:
                    window.handle_hotkey_trigger("pad-02")
                    trigger.assert_called_once_with(window.config.pads[1])

                window.update_remote_status("stopped", "")
                self.assertEqual(window.start_server_button.text(), "Start")
                self.assertTrue(window.start_server_button.isEnabled())
                self.assertFalse(window.stop_server_button.isEnabled())

                window.update_remote_status("running", "")
                self.assertEqual(window.start_server_button.text(), "Restart")
                self.assertTrue(window.stop_server_button.isEnabled())
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_shortcut_assignment_and_clear_apply_without_saving_the_pad(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                window.refresh_editor()
                with patch.object(window.hotkey_manager, "register_pads", return_value={}):
                    window.hotkey_edit.setKeySequence(QKeySequence("Ctrl+Alt+4"))

                    saved = json.loads(paths.config_path.read_text(encoding="utf-8"))
                    self.assertEqual(window.config.pads[0].hotkey, "Ctrl+Alt+4")
                    self.assertEqual(saved["pads"][0]["hotkey"], "Ctrl+Alt+4")
                    self.assertIn("Active globally", window.hotkey_edit.toolTip())

                    window.hotkey_edit.setKeySequence(QKeySequence("Ctrl+Alt+Num+4"))
                    self.assertEqual(window.config.pads[0].hotkey, "Ctrl+Alt+Num+4")

                    window.clear_hotkey_button.click()

                    cleared = json.loads(paths.config_path.read_text(encoding="utf-8"))
                    self.assertEqual(window.config.pads[0].hotkey, "")
                    self.assertEqual(cleared["pads"][0]["hotkey"], "")
            finally:
                window.close()
                window.audio_engine.shutdown()

    def test_shortcut_capture_distinguishes_top_row_and_numpad_digits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = root / "runtime"
            paths = AppPaths(
                app_dir=root,
                data_dir=root / "data",
                runtime_dir=runtime,
                sounds_dir=runtime / "sounds",
                images_dir=runtime / "images",
                config_path=root / "data" / "soundboard_config.json",
                resources_dir=Path(__file__).resolve().parents[1]
                / "src"
                / "python_qt6"
                / "soundboard_qt6",
            )
            paths.ensure()
            window = MainWindow(paths)
            try:
                modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                with patch.object(window.hotkey_manager, "register_pads", return_value={}):
                    top_row_event = QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_4,
                        modifiers,
                        0x05,
                        0x34,
                        0,
                        "4",
                    )
                    QApplication.sendEvent(window.hotkey_edit, top_row_event)
                    self.assertEqual(window.config.pads[0].hotkey, "Ctrl+Alt+4")

                    numpad_event = QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_4,
                        modifiers | Qt.KeyboardModifier.KeypadModifier,
                        0x4B,
                        0x64,
                        0,
                        "4",
                    )
                    QApplication.sendEvent(window.hotkey_edit, numpad_event)
                    self.assertEqual(window.config.pads[0].hotkey, "Ctrl+Alt+Num+4")
                    self.assertEqual(
                        window.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
                        "Ctrl+Alt+Num+4",
                    )
            finally:
                window.close()
                window.audio_engine.shutdown()


class WindowsSecurityTests(unittest.TestCase):
    def test_signature_command_quotes_apostrophes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="O'Brien-") as temporary_dir:
            executable = Path(temporary_dir) / "helper.exe"
            executable.write_bytes(b"executable")

            completed = type("Completed", (), {"returncode": 0, "stdout": "Valid\n"})()
            with patch("soundboard_qt6.windows_security.subprocess.run", return_value=completed) as run:
                self.assertTrue(is_authenticode_signature_valid(executable))

            command_text = run.call_args.args[0][-1]
            self.assertIn("O''Brien", command_text)

    def test_signed_cable_extraction_rejects_parent_directory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            archive_path = root / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.exe", b"unsafe")

            with self.assertRaisesRegex(ValueError, "unsafe file path"):
                MainWindow._extract_signed_cable_package(archive_path, root / "extract")

            self.assertFalse((root / "outside.exe").exists())


class SoundValidationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_warning_uses_wrapped_non_selectable_text(self) -> None:
        parent = QWidget()
        dialog = SoundValidationDialog(
            parent,
            "Sound files must be 15 seconds or shorter. example.mp3 is 18.5 seconds, so it was not added.",
        )

        body = dialog.findChild(QLabel, "SoundWarningBody")
        ok_button = dialog.findChild(QPushButton, "SoundWarningOk")
        self.assertIsNotNone(body)
        self.assertIsNotNone(ok_button)
        self.assertTrue(body.wordWrap())
        self.assertEqual(body.textInteractionFlags(), Qt.TextInteractionFlag.NoTextInteraction)
        self.assertEqual(ok_button.text(), "OK")
        self.assertEqual(dialog.width(), 520)
        dialog.close()
        parent.close()


if __name__ == "__main__":
    unittest.main()
