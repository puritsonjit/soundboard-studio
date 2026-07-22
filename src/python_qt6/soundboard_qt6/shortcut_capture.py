from __future__ import annotations

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit


_MODIFIER_KEYS = {
    int(Qt.Key.Key_Shift),
    int(Qt.Key.Key_Control),
    int(Qt.Key.Key_Alt),
    int(Qt.Key.Key_AltGr),
    int(Qt.Key.Key_Meta),
}
_CAPTURED_MODIFIERS = (
    Qt.KeyboardModifier.ShiftModifier
    | Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
    | Qt.KeyboardModifier.KeypadModifier
)
_NUMPAD_VIRTUAL_KEYS = {
    0x6A: int(Qt.Key.Key_Asterisk),
    0x6B: int(Qt.Key.Key_Plus),
    0x6D: int(Qt.Key.Key_Minus),
    0x6E: int(Qt.Key.Key_Period),
    0x6F: int(Qt.Key.Key_Slash),
}


class ShortcutCaptureEdit(QKeySequenceEdit):
    """Capture one physical shortcut while preserving Windows numpad keys."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.isAutoRepeat() or int(event.key()) in _MODIFIER_KEYS:
            event.accept()
            return

        key = int(event.key())
        modifiers = event.modifiers() & _CAPTURED_MODIFIERS
        native_virtual_key = int(event.nativeVirtualKey())

        # QKeySequenceEdit can collapse VK_NUMPAD0..9 into the matching top-row
        # digit. Rebuild the sequence from the native key before that happens.
        if 0x60 <= native_virtual_key <= 0x69:
            key = ord("0") + native_virtual_key - 0x60
            modifiers |= Qt.KeyboardModifier.KeypadModifier
        elif native_virtual_key in _NUMPAD_VIRTUAL_KEYS:
            key = _NUMPAD_VIRTUAL_KEYS[native_virtual_key]
            modifiers |= Qt.KeyboardModifier.KeypadModifier
        elif 0x30 <= native_virtual_key <= 0x39:
            key = ord("0") + native_virtual_key - 0x30
            modifiers &= ~Qt.KeyboardModifier.KeypadModifier

        combination = QKeyCombination(modifiers, Qt.Key(key))
        self.setKeySequence(QKeySequence(combination))
        event.accept()
