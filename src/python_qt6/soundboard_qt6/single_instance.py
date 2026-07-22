from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys


ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\SoundboardStudio-Mixer-v1"


class SingleInstanceGuard:
    """Own a mutex so two mixers cannot fight over devices and settings."""

    def __init__(self, identity_path: Path) -> None:
        del identity_path
        self._handle = None
        self.acquired = True
        if sys.platform != "win32":
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        self.acquired = ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._kernel32.CloseHandle(handle)

    def __enter__(self) -> "SingleInstanceGuard":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
