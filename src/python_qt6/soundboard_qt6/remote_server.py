from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import socket
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web
from PySide6.QtCore import QObject, Signal


ACCESS_COOKIE_NAME = "soundboard_remote_access"
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


class BrowserRemoteServer(QObject):
    log = Signal(str)
    status_changed = Signal(str, str)
    trigger_requested = Signal(str)
    stop_requested = Signal()
    playback_mode_changed = Signal(str)
    client_count_changed = Signal(int)
    urls_changed = Signal(list)

    def __init__(
        self,
        web_dir: Path,
        port: int,
        state_provider: Callable[[], dict],
        image_resolver: Callable[[str], Path | None],
    ) -> None:
        super().__init__()
        self._web_dir = web_dir
        self._port = port
        self._state_provider = state_provider
        self._image_resolver = image_resolver
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._state_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._status = "stopped"
        self._status_detail = ""
        self._access_token = ""

    @property
    def port(self) -> int:
        return self._port

    @property
    def status(self) -> str:
        with self._state_lock:
            return self._status

    def is_active(self) -> bool:
        with self._state_lock:
            return self._status in {"starting", "running"}

    def page_urls(self) -> list[str]:
        addresses = self._candidate_ipv4_addresses()
        primary_address = self._primary_ipv4_address()
        if primary_address and primary_address in addresses:
            addresses.remove(primary_address)
            addresses.insert(0, primary_address)

        urls = [f"http://127.0.0.1:{self._port}/"]
        for ip in addresses:
            urls.append(f"http://{ip}:{self._port}/")
        return list(dict.fromkeys(urls))

    def primary_page_url(self) -> str:
        urls = self.page_urls()
        for url in urls:
            if "127.0.0.1" not in url:
                return url
        return urls[0]

    def set_access_token(self, token: str | None) -> None:
        normalized = str(token or "").strip()
        with self._state_lock:
            changed = normalized != self._access_token
            self._access_token = normalized
            loop = self._loop
        if changed and loop is not None and not loop.is_closed():
            coroutine = self._disconnect_clients()
            try:
                future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                future.add_done_callback(self._consume_background_result)
            except RuntimeError:
                coroutine.close()

    def start(self) -> bool:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                self.status_changed.emit(self._status, self._status_detail)
                return False
            self._stop_requested.clear()
            self._set_status_locked("starting", "")
            self._thread = threading.Thread(target=self._run, daemon=True)
            # Emit before the worker can publish running/failed, otherwise a
            # very fast startup can leave the UI stuck on a late starting event.
            self.status_changed.emit("starting", "")
            self.urls_changed.emit(self.page_urls())
            self._thread.start()
        return True

    def stop(self) -> None:
        with self._state_lock:
            loop = self._loop
            thread = self._thread
            status = self._status

        self._stop_requested.set()

        if thread is not None and thread is threading.current_thread():
            return

        if status == "starting" or loop is None or loop.is_closed():
            if thread is not None and thread.is_alive():
                thread.join(timeout=4)
            if thread is not None and thread.is_alive():
                self.log.emit("Browser remote shutdown timed out while the server was starting.")
                return
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
                if self._loop is loop:
                    self._loop = None
                self._runner = None
                self._set_status_locked("stopped", "")
            self.client_count_changed.emit(0)
            self.status_changed.emit("stopped", "")
            return

        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            future.result(timeout=3)
        except Exception as exc:
            self.log.emit(f"Browser remote shutdown timeout/error: {exc}")
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

        if thread is not None and thread.is_alive():
            self.log.emit("Browser remote is still closing; a second server was not started.")
            return

        with self._state_lock:
            if self._thread is thread:
                self._thread = None
            if self._loop is loop:
                self._loop = None
            self._runner = None
        self.client_count_changed.emit(0)

    def _set_status_locked(self, status: str, detail: str = "") -> None:
        self._status = status
        self._status_detail = detail

    def _set_status(self, status: str, detail: str = "") -> None:
        with self._state_lock:
            self._set_status_locked(status, detail)
        self.status_changed.emit(status, detail)

    def broadcast_state(self) -> None:
        self._schedule_broadcast("state_sync", self._state_snapshot())

    def broadcast_playback_state(self, pad_ids: list[str]) -> None:
        self._schedule_broadcast("playback_state", {"activePadIds": list(pad_ids)})

    def broadcast_audio_status(self, ready: bool) -> None:
        self._schedule_broadcast("audio_status", {"audioReady": bool(ready)})

    def _schedule_broadcast(self, message_type: str, payload: dict) -> None:
        with self._state_lock:
            loop = self._loop
        if loop is None or loop.is_closed():
            return
        coroutine = self._broadcast(message_type, payload)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            future.add_done_callback(self._consume_background_result)
        except RuntimeError:
            coroutine.close()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(self._handle_loop_exception)
        with self._state_lock:
            self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            if self._stop_requested.is_set():
                self._set_status("stopped", "")
                return
            loop.run_until_complete(self._start_site())
            if self._stop_requested.is_set():
                self._set_status("stopped", "")
            else:
                self._set_status("running", "")
                loop.run_forever()
        except Exception as exc:
            detail = str(exc)
            self._set_status("failed", detail)
            self.client_count_changed.emit(0)
            self.log.emit(f"Browser remote server failed to start: {detail}")
        finally:
            if self._runner is not None:
                try:
                    loop.run_until_complete(self._runner.cleanup())
                except Exception:
                    pass
            with self._state_lock:
                self._runner = None
                if self._thread is threading.current_thread():
                    self._thread = None
            loop.close()
            with self._state_lock:
                if self._loop is loop:
                    self._loop = None
                current_status = self._status
        if current_status == "running":
            self._set_status("stopped", "")

    def _handle_loop_exception(self, loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exception = context.get("exception")
        if isinstance(exception, ConnectionResetError):
            return
        if isinstance(exception, OSError) and getattr(exception, "winerror", None) == 10054:
            return
        loop.default_exception_handler(context)

    async def _start_site(self) -> None:
        app = web.Application(middlewares=[self._authentication_middleware])
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/connect/{access_token}", self._handle_connect)
        app.router.add_get("/app.css", self._handle_css)
        app.router.add_get("/app.js", self._handle_js)
        app.router.add_get("/brand-logo.png", self._handle_brand_logo)
        app.router.add_get("/pad-image/{pad_id}", self._handle_pad_image)
        app.router.add_get("/ws", self._handle_ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await site.start()

    @web.middleware
    async def _authentication_middleware(self, request: web.Request, handler):
        with self._state_lock:
            access_token = self._access_token
        if not access_token:
            return await handler(request)

        supplied_query_token = request.query.get("access", "")
        supplied_path_token = request.match_info.get("access_token", "")
        supplied_cookie_token = request.cookies.get(ACCESS_COOKIE_NAME, "")
        query_matches = bool(supplied_query_token) and hmac.compare_digest(supplied_query_token, access_token)
        path_matches = bool(supplied_path_token) and hmac.compare_digest(supplied_path_token, access_token)
        cookie_matches = bool(supplied_cookie_token) and hmac.compare_digest(supplied_cookie_token, access_token)
        if query_matches or path_matches:
            response = web.HTTPFound(location="/")
            is_secure = request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
            response.set_cookie(
                ACCESS_COOKIE_NAME,
                access_token,
                httponly=True,
                secure=is_secure,
                # Lax survives a QR scanner/browser handoff while still excluding
                # the cookie from cross-site WebSocket and subresource requests.
                samesite="Lax",
                path="/",
            )
            raise response
        if cookie_matches:
            return await handler(request)
        return web.Response(
            status=401,
            text=(
                "<!doctype html><html><head><meta name='viewport' content='width=device-width'>"
                "<title>Private remote</title></head><body>"
                "<h1>Private remote</h1><p>Scan the current QR code in Soundboard Studio to connect.</p>"
                "</body></html>"
            ),
            content_type="text/html",
            headers=self._no_store_headers(),
        )

    async def _shutdown(self) -> None:
        for client in list(self._clients):
            await client.close()
        self._clients.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self.client_count_changed.emit(0)
        self._set_status("stopped", "")
        if self._loop is not None:
            self._loop.call_soon(self._loop.stop)

    async def _disconnect_clients(self) -> None:
        for client in list(self._clients):
            await client.close(code=1008, message=b"Remote access changed")
        self._clients.clear()
        self.client_count_changed.emit(0)

    async def _handle_index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self._web_dir / "index.html", headers=self._no_store_headers())

    async def _handle_connect(self, request: web.Request) -> web.StreamResponse:
        # Authentication middleware consumes valid links before this handler.
        # In local mode, keep a stale Internet QR link harmless and useful.
        raise web.HTTPFound(location="/")

    async def _handle_css(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self._web_dir / "app.css", headers=self._no_store_headers())

    async def _handle_js(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self._web_dir / "app.js", headers=self._no_store_headers())

    async def _handle_brand_logo(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self._web_dir / "brand-logo.png", headers=self._no_store_headers())

    async def _handle_pad_image(self, request: web.Request) -> web.StreamResponse:
        pad_id = request.match_info["pad_id"]
        image_path = self._image_resolver(pad_id)
        if image_path is None or not image_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(image_path, headers=self._versioned_asset_headers())

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        with self._state_lock:
            access_token = self._access_token
        origin = request.headers.get("Origin", "")
        if not self._websocket_request_allowed(access_token, origin, request.host):
            raise web.HTTPForbidden(text="WebSocket origin is not allowed.")

        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        self._clients.add(ws)
        self.client_count_changed.emit(len(self._clients))

        try:
            await ws.send_json({"type": "state_sync", "payload": self._state_snapshot()})
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                decoded = self._decode_client_message(message.data)
                if decoded is None:
                    continue
                message_type, message_payload = decoded

                if message_type == "trigger_pad":
                    pad_id = str(message_payload.get("padId", "")).strip()
                    if pad_id and len(pad_id) <= 64:
                        self.trigger_requested.emit(pad_id)
                elif message_type == "stop_all":
                    self.stop_requested.emit()
                elif message_type == "set_playback_mode":
                    mode = str(message_payload.get("mode", "")).strip().lower()
                    if mode in {"layer", "replace"}:
                        self.playback_mode_changed.emit(mode)
                elif message_type == "ping":
                    await ws.send_json({"type": "pong", "payload": {}})
        finally:
            self._clients.discard(ws)
            self.client_count_changed.emit(len(self._clients))

        return ws

    async def _broadcast(self, message_type: str, payload: dict) -> None:
        dead_clients: list[web.WebSocketResponse] = []
        for client in list(self._clients):
            if client.closed:
                dead_clients.append(client)
                continue
            try:
                await client.send_json({"type": message_type, "payload": payload})
            except Exception:
                dead_clients.append(client)
        for client in dead_clients:
            self._clients.discard(client)
        self.client_count_changed.emit(len(self._clients))

    @staticmethod
    def _no_store_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'"
            ),
        }

    @staticmethod
    def _versioned_asset_headers() -> dict[str, str]:
        return {"Cache-Control": "public, max-age=31536000, immutable"}

    @staticmethod
    def _consume_background_result(future) -> None:
        try:
            future.result()
        except (asyncio.CancelledError, Exception):
            pass

    @staticmethod
    def _origin_matches_host(origin: str, request_host: str) -> bool:
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            origin_host = parsed.hostname.lower().rstrip(".")
            request = urlsplit(f"//{str(request_host).strip().lower()}")
            request_hostname = str(request.hostname or "").rstrip(".")
            if origin_host != request_hostname:
                return False
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if request.port is None:
                return parsed.port is None
            return origin_port == request.port
        except (TypeError, ValueError):
            return False

    @classmethod
    def _websocket_request_allowed(
        cls,
        access_token: str,
        origin: str,
        request_host: str,
    ) -> bool:
        if origin and not cls._origin_matches_host(origin, request_host):
            return False
        return bool(access_token) or cls._is_literal_local_host(request_host)

    @staticmethod
    def _is_literal_local_host(request_host: str) -> bool:
        try:
            parsed = urlsplit(f"//{str(request_host).strip().lower()}")
            hostname = str(parsed.hostname or "").rstrip(".")
            if hostname == "localhost":
                return True
            address = ipaddress.ip_address(hostname)
            return bool(
                address.version == 4
                and not address.is_multicast
                and not address.is_unspecified
                and (
                    address.is_private
                    or address.is_loopback
                    or address.is_link_local
                    or address in _SHARED_ADDRESS_SPACE
                )
            )
        except (TypeError, ValueError):
            return False

    def _state_snapshot(self) -> dict:
        try:
            state = self._state_provider()
        except Exception as exc:
            self.log.emit(f"Could not refresh the phone remote state: {exc}")
            return {}
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _decode_client_message(data: str) -> tuple[str, dict] | None:
        try:
            message = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(message, dict):
            return None

        message_type = message.get("type")
        if not isinstance(message_type, str) or not message_type:
            return None
        payload = message.get("payload")
        return message_type, payload if isinstance(payload, dict) else {}

    @staticmethod
    def _candidate_ipv4_addresses() -> list[str]:
        addresses: set[str] = set()
        host_name = socket.gethostname()
        try:
            for result in socket.getaddrinfo(host_name, None, socket.AF_INET):
                ip = result[4][0]
                if BrowserRemoteServer._is_remote_address(ip):
                    addresses.add(ip)
        except socket.gaierror:
            pass
        primary = BrowserRemoteServer._primary_ipv4_address()
        if primary:
            addresses.add(primary)
        return sorted(addresses, key=BrowserRemoteServer._address_priority)

    @staticmethod
    def _primary_ipv4_address() -> str | None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = str(probe.getsockname()[0])
            return address if BrowserRemoteServer._is_remote_address(address) else None
        except OSError:
            return None
        finally:
            probe.close()

    @staticmethod
    def _is_remote_address(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return bool(
            address.version == 4
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_multicast
            and not address.is_unspecified
            and (address.is_private or address in _SHARED_ADDRESS_SPACE)
        )

    @staticmethod
    def _address_priority(value: str) -> tuple[int, str]:
        address = ipaddress.ip_address(value)
        if value.startswith("192.168."):
            rank = 0
        elif value.startswith("10."):
            rank = 1
        elif address.is_private:
            rank = 2
        else:
            rank = 3
        return rank, value
