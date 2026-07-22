# Phone Remote

## Connection Modes

Local mode serves HTTP and WebSocket traffic from port 8765 by default. The
phone and PC must be on the same reachable LAN; guest-network client isolation
can block the connection.

Internet mode starts an app-owned cloudflared Quick Tunnel and produces a
temporary HTTPS/WSS QR link. The helper is downloaded into `runtime/tools`, is
not installed globally, runs without a console window, and stops with the app.
Internet mode requires outbound Internet and depends on the availability of the
third-party Quick Tunnel service.

## Browser Behavior

- One tap, click, or touch triggers the complete clip without requiring a hold.
- Stack allows simultaneous sounds; Solo stops previous sounds first.
- Stop All immediately ends active remote/shortcut sounds.
- Fullscreen affects only the browser page.
- Controls are disabled while the desktop audio route is unavailable.
- The page automatically reconnects after short network interruptions.

## Security

Local mode is intended for a trusted private network and validates browser
origins and local hosts. Internet mode creates a random access token, puts it in
the QR session link, exchanges it for an HttpOnly SameSite cookie, and rejects
requests without the current session. Restarting or stopping the remote
invalidates the previous public link.

## WebSocket Messages

Browser to desktop:

- `trigger_pad` with `padId`
- `stop_all`
- `set_playback_mode` with `layer` or `exclusive`
- `ping`

Desktop to browser:

- `state_sync`
- `playback_state`
- `audio_status`
- `pong`

The current implementation in `remote_server.py` is the protocol source of
truth. Unknown or malformed client messages are ignored safely.
