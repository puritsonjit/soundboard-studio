from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import re
import subprocess
import threading
import urllib.request

from PySide6.QtCore import QObject, Signal

from .windows_security import is_authenticode_signature_valid


CLOUDFLARED_DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)
CLOUDFLARED_FILENAME = "cloudflared.exe"
MAX_HELPER_BYTES = 100 * 1024 * 1024
TUNNEL_START_TIMEOUT_SECONDS = 45.0
_PUBLIC_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def extract_public_url(output_line: str) -> str:
    match = _PUBLIC_URL_PATTERN.search(str(output_line))
    return match.group(0).rstrip("/") if match else ""


class InternetTunnel(QObject):
    status_changed = Signal(str, str)
    url_changed = Signal(str)
    log = Signal(str)

    def __init__(self, tools_dir: Path) -> None:
        super().__init__()
        self._tools_dir = tools_dir
        self._helper_path = tools_dir / CLOUDFLARED_FILENAME
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._stop_requested = threading.Event()
        self._lock = threading.RLock()
        self._generation = 0
        self._status = "stopped"
        self._detail = ""
        self._public_url = ""

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def public_url(self) -> str:
        with self._lock:
            return self._public_url

    def start(self, port: int) -> None:
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._stop_requested.clear()
            self._status = "starting"
            self._detail = ""
            self._public_url = ""
            self._thread = threading.Thread(target=self._run, args=(generation, port), daemon=True)
            # Publish starting before the worker can report running or failed.
            self.status_changed.emit("starting", "")
            self.url_changed.emit("")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop_requested.set()
            process = self._process
            thread = self._thread
            was_active = self._status != "stopped" or bool(self._public_url)

        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=2.5)

        with self._lock:
            if self._process is process:
                self._process = None
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._status = "stopped"
            self._detail = ""
            self._public_url = ""
        if was_active:
            self.url_changed.emit("")
            self.status_changed.emit("stopped", "")

    def _run(self, generation: int, port: int) -> None:
        recent_output: deque[str] = deque(maxlen=4)
        startup_timed_out = threading.Event()
        startup_watchdog: threading.Timer | None = None
        try:
            helper_path = self._ensure_helper(generation)
            if not self._is_current(generation):
                return

            environment = os.environ.copy()
            isolated_home = self._tools_dir / "cloudflared-home"
            isolated_home.mkdir(parents=True, exist_ok=True)
            environment["HOME"] = str(isolated_home)
            environment["USERPROFILE"] = str(isolated_home)
            command = [
                str(helper_path),
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{int(port)}",
                "--loglevel",
                "info",
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=str(self._tools_dir),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            with self._lock:
                if not self._is_current_locked(generation):
                    process.terminate()
                    return
                self._process = process

            def stop_stalled_startup() -> None:
                if self._is_current(generation) and not self.public_url and process.poll() is None:
                    startup_timed_out.set()
                    try:
                        process.terminate()
                    except OSError:
                        pass

            startup_watchdog = threading.Timer(TUNNEL_START_TIMEOUT_SECONDS, stop_stalled_startup)
            startup_watchdog.daemon = True
            startup_watchdog.start()

            assert process.stdout is not None
            for output_line in process.stdout:
                line = output_line.strip()
                if line:
                    recent_output.append(line)
                public_url = extract_public_url(line)
                if public_url and self._is_current(generation):
                    startup_watchdog.cancel()
                    with self._lock:
                        self._status = "running"
                        self._detail = ""
                        self._public_url = public_url
                    self.url_changed.emit(public_url)
                    self.status_changed.emit("running", "")
                if not self._is_current(generation):
                    break

            exit_code = process.wait()
            if not self._is_current(generation):
                return
            if startup_timed_out.is_set():
                detail = f"Internet link setup timed out after {TUNNEL_START_TIMEOUT_SECONDS:g} seconds."
            elif self.public_url:
                detail = f"Internet link closed unexpectedly (exit code {exit_code})."
            else:
                detail = self._failure_detail(recent_output, exit_code)
            self._set_failed(generation, detail)
        except Exception as exc:
            if self._is_current(generation):
                self._set_failed(generation, str(exc))
        finally:
            if startup_watchdog is not None:
                startup_watchdog.cancel()
            with self._lock:
                if self._generation == generation:
                    self._process = None
                    self._thread = None

    def _ensure_helper(self, generation: int) -> Path:
        if self._valid_helper(self._helper_path):
            return self._helper_path

        self._tools_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = self._helper_path.with_name(f".{self._helper_path.name}.{generation}.download")
        temporary_path.unlink(missing_ok=True)
        self.log.emit("Preparing the secure Internet remote helper.")
        request = urllib.request.Request(
            CLOUDFLARED_DOWNLOAD_URL,
            headers={"User-Agent": "Soundboard-Studio/1.0"},
        )
        downloaded = 0
        try:
            with urllib.request.urlopen(request, timeout=30) as response, temporary_path.open("wb") as output:
                while True:
                    if not self._is_current(generation):
                        raise RuntimeError("Internet link setup was cancelled.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_HELPER_BYTES:
                        raise ValueError("The Internet remote helper download is unexpectedly large.")
                    output.write(chunk)
            if not self._valid_helper(temporary_path):
                raise ValueError("The downloaded Internet remote helper is not a valid Windows application.")
            os.replace(temporary_path, self._helper_path)
            return self._helper_path
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _valid_helper(path: Path) -> bool:
        try:
            if path.stat().st_size < 5 * 1024 * 1024:
                return False
            with path.open("rb") as stream:
                is_windows_executable = stream.read(2) == b"MZ"
            return is_windows_executable and is_authenticode_signature_valid(path)
        except OSError:
            return False

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return self._is_current_locked(generation)

    def _is_current_locked(self, generation: int) -> bool:
        return self._generation == generation and not self._stop_requested.is_set()

    def _set_failed(self, generation: int, detail: str) -> None:
        with self._lock:
            if not self._is_current_locked(generation):
                return
            self._status = "failed"
            self._detail = detail
            self._public_url = ""
        self.url_changed.emit("")
        self.status_changed.emit("failed", detail)

    @staticmethod
    def _failure_detail(output: deque[str], exit_code: int) -> str:
        for line in reversed(output):
            lower = line.lower()
            if "error" in lower or "failed" in lower:
                return f"Internet link failed: {line[:240]}"
        return f"Internet link failed before a public URL was created (exit code {exit_code})."
