from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def is_authenticode_signature_valid(file_path: Path) -> bool:
    if sys.platform != "win32" or not file_path.is_file():
        return False
    try:
        literal_path = "'" + str(file_path).replace("'", "''") + "'"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"(Get-AuthenticodeSignature -LiteralPath {literal_path}).Status",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            **hidden_subprocess_kwargs(),
        )
        return completed.returncode == 0 and completed.stdout.strip() == "Valid"
    except Exception:
        return False
