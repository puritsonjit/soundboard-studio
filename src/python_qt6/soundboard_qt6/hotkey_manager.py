from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Iterable

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

from .models import PadConfig


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID_BASE = 0x5300


_SPECIAL_KEYS = {
    int(Qt.Key.Key_Backspace): 0x08,
    int(Qt.Key.Key_Tab): 0x09,
    int(Qt.Key.Key_Clear): 0x0C,
    int(Qt.Key.Key_Return): 0x0D,
    int(Qt.Key.Key_Enter): 0x0D,
    int(Qt.Key.Key_Pause): 0x13,
    int(Qt.Key.Key_CapsLock): 0x14,
    int(Qt.Key.Key_Escape): 0x1B,
    int(Qt.Key.Key_Space): 0x20,
    int(Qt.Key.Key_PageUp): 0x21,
    int(Qt.Key.Key_PageDown): 0x22,
    int(Qt.Key.Key_End): 0x23,
    int(Qt.Key.Key_Home): 0x24,
    int(Qt.Key.Key_Left): 0x25,
    int(Qt.Key.Key_Up): 0x26,
    int(Qt.Key.Key_Right): 0x27,
    int(Qt.Key.Key_Down): 0x28,
    int(Qt.Key.Key_Print): 0x2C,
    int(Qt.Key.Key_Insert): 0x2D,
    int(Qt.Key.Key_Delete): 0x2E,
    int(Qt.Key.Key_NumLock): 0x90,
    int(Qt.Key.Key_ScrollLock): 0x91,
}


class HotkeyError(ValueError):
    pass


def normalize_hotkey(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.isEmpty() or sequence.count() != 1:
        raise HotkeyError("Use one shortcut combination, such as Ctrl+Shift+1.")
    parse_hotkey(sequence)
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def describe_hotkey_key_source(sequence_or_text: QKeySequence | str) -> str:
    sequence = (
        sequence_or_text
        if isinstance(sequence_or_text, QKeySequence)
        else QKeySequence.fromString(str(sequence_or_text), QKeySequence.SequenceFormat.PortableText)
    )
    if sequence.isEmpty() or sequence.count() != 1:
        return "Not set"

    combination = sequence[0]
    key = int(combination.key())
    keypad = bool(combination.keyboardModifiers() & Qt.KeyboardModifier.KeypadModifier)
    if keypad:
        if ord("0") <= key <= ord("9"):
            return f"Numpad {chr(key)}"
        keypad_names = {
            ord("*"): "Numpad Multiply",
            ord("+"): "Numpad Plus",
            ord("-"): "Numpad Minus",
            ord("."): "Numpad Decimal",
            ord("/"): "Numpad Divide",
            int(Qt.Key.Key_Enter): "Numpad Enter",
            int(Qt.Key.Key_Return): "Numpad Enter",
        }
        return keypad_names.get(key, "Numpad key")
    if ord("0") <= key <= ord("9"):
        return f"Top-row {chr(key)}"
    return "Standard key"


def parse_hotkey(sequence_or_text: QKeySequence | str) -> tuple[int, int]:
    sequence = (
        sequence_or_text
        if isinstance(sequence_or_text, QKeySequence)
        else QKeySequence.fromString(str(sequence_or_text), QKeySequence.SequenceFormat.PortableText)
    )
    if sequence.isEmpty() or sequence.count() != 1:
        raise HotkeyError("Use one shortcut combination, such as Ctrl+Shift+1.")

    combination = sequence[0]
    key = int(combination.key())
    qt_modifiers = combination.keyboardModifiers()
    modifiers = 0
    if qt_modifiers & Qt.KeyboardModifier.ControlModifier:
        modifiers |= MOD_CONTROL
    if qt_modifiers & Qt.KeyboardModifier.AltModifier:
        modifiers |= MOD_ALT
    if qt_modifiers & Qt.KeyboardModifier.ShiftModifier:
        modifiers |= MOD_SHIFT
    if qt_modifiers & Qt.KeyboardModifier.MetaModifier:
        modifiers |= MOD_WIN

    keypad = bool(qt_modifiers & Qt.KeyboardModifier.KeypadModifier)
    virtual_key, implied_modifiers = _virtual_key_for_qt_key(key, keypad)
    if virtual_key == 0:
        raise HotkeyError("That key is not supported as a global shortcut.")
    return modifiers | implied_modifiers, virtual_key


def _virtual_key_for_qt_key(key: int, keypad: bool) -> tuple[int, int]:
    if keypad:
        if ord("0") <= key <= ord("9"):
            return 0x60 + key - ord("0"), 0
        keypad_keys = {
            ord("*"): 0x6A,
            ord("+"): 0x6B,
            ord("-"): 0x6D,
            ord("."): 0x6E,
            ord("/"): 0x6F,
            int(Qt.Key.Key_Enter): 0x0D,
            int(Qt.Key.Key_Return): 0x0D,
        }
        if key in keypad_keys:
            return keypad_keys[key], 0

    if ord("A") <= key <= ord("Z") or ord("0") <= key <= ord("9"):
        return key, 0
    if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
        return 0x70 + key - int(Qt.Key.Key_F1), 0
    if key in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[key], 0

    if 0x20 <= key <= 0x7E and sys.platform == "win32":
        scan = int(ctypes.windll.user32.VkKeyScanW(chr(key)))
        if scan != -1:
            implied = 0
            shift_state = (scan >> 8) & 0xFF
            if shift_state & 1:
                implied |= MOD_SHIFT
            if shift_state & 2:
                implied |= MOD_CONTROL
            if shift_state & 4:
                implied |= MOD_ALT
            return scan & 0xFF, implied
    return 0, 0


class _WindowsHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeyManager") -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        del event_type
        try:
            native_message = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        except (TypeError, ValueError):
            return False, 0
        if native_message.message == WM_HOTKEY:
            self._owner.dispatch(int(native_message.wParam))
            return True, 0
        return False, 0


class GlobalHotkeyManager(QObject):
    triggered = Signal(str)
    registration_failed = Signal(str, str, str)

    def __init__(self, application) -> None:
        super().__init__()
        self._application = application
        self._bindings: dict[int, str] = {}
        self._filter: _WindowsHotkeyEventFilter | None = None
        self._user32 = None
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self._user32.RegisterHotKey.restype = wintypes.BOOL
            self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.UnregisterHotKey.restype = wintypes.BOOL
            self._filter = _WindowsHotkeyEventFilter(self)
            self._application.installNativeEventFilter(self._filter)

    def register_pads(self, pads: Iterable[PadConfig]) -> dict[str, str]:
        self.unregister_all()
        failures: dict[str, str] = {}
        seen: dict[tuple[int, int], str] = {}
        for index, pad in enumerate(pads):
            if not pad.hotkey.strip():
                continue
            try:
                canonical = normalize_hotkey(pad.hotkey)
                modifiers, virtual_key = parse_hotkey(canonical)
            except HotkeyError as exc:
                failures[pad.id] = str(exc)
                self.registration_failed.emit(pad.id, pad.hotkey, str(exc))
                continue

            signature = modifiers, virtual_key
            if signature in seen:
                reason = f"Already used by {seen[signature]}."
                failures[pad.id] = reason
                self.registration_failed.emit(pad.id, canonical, reason)
                continue
            seen[signature] = pad.id

            if self._user32 is None:
                reason = "Global shortcuts are available on Windows only."
                failures[pad.id] = reason
                self.registration_failed.emit(pad.id, canonical, reason)
                continue

            hotkey_id = HOTKEY_ID_BASE + index
            ctypes.set_last_error(0)
            registered = bool(
                self._user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, virtual_key)
            )
            if not registered:
                reason = "This shortcut is already used by Windows or another app."
                failures[pad.id] = reason
                self.registration_failed.emit(pad.id, canonical, reason)
                continue
            self._bindings[hotkey_id] = pad.id
        return failures

    def unregister_all(self) -> None:
        if self._user32 is not None:
            for hotkey_id in tuple(self._bindings):
                self._user32.UnregisterHotKey(None, hotkey_id)
        self._bindings.clear()

    def dispatch(self, hotkey_id: int) -> None:
        pad_id = self._bindings.get(hotkey_id)
        if pad_id:
            self.triggered.emit(pad_id)

    def close(self) -> None:
        self.unregister_all()
        if self._filter is not None:
            self._application.removeNativeEventFilter(self._filter)
            self._filter = None
