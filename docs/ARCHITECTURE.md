# Architecture

## Runtime Flow

```text
Phone browser / global shortcut
            |
            v
      Qt6 desktop app ----> soundboard-only local playback
            |
            v
 selected real mic + pad audio
            |
            v
 CABLE Input (VB-CABLE playback endpoint)
            |
            v
 CABLE Output (select this microphone in Discord/TeamSpeak/OBS)
```

The phone sends control messages only. Audio files never travel through the
phone or network.

## Desktop Application

`src/python_qt6/main.py` starts the borderless loading screen and then the Qt6
main window. The app owns configuration, managed assets, audio streams, global
shortcuts, the HTTP/WebSocket server, and the optional Internet tunnel.

There are 24 pads. A pad can use one sound or randomly select from a managed
sound pack. Files are copied into `runtime` when saved, so removing an original
source file does not break a configured pad. Audio longer than 15 seconds is
rejected at load, import, export, and playback boundaries; the clip editor can
create a compliant segment.

Clicking a desktop pad selects it without playing it. `Preview locally` sends
audio only to the selected headphones/speakers. Phone and global-shortcut
triggers send pad audio to the virtual microphone and optionally to local
playback.

## Audio Engine

The engine runs at 48 kHz float32. It prefers one synchronized duplex stream for
the selected microphone and monitor device, then falls back to independent
streams for wireless or mixed-driver hardware.

The monitor path contains soundboard audio only. The virtual path downmixes to a
voice-app-safe dual-mono signal, applies the optional microphone gate, combines
mic and pad audio, and protects the final peak with a smooth limiter.

VB-CABLE normally uses a 256-frame callback, a two-block safety queue, and a
20 ms requested output buffer. A larger compatibility profile is attempted only
if the low-latency stream cannot start.

## Persistent Files

The packaged app stores all mutable files beside the EXE:

```text
data/soundboard_config.json
runtime/sounds/
runtime/images/
runtime/tools/cloudflared.exe   (Internet mode only)
driver/                         (virtual mic setup only)
```

Profile ZIP files include configuration and referenced managed assets. They do
not include the EXE, VB-CABLE driver, or cloudflared helper.

## Platform Boundary

The app is portable, but a Windows audio driver cannot be embedded as a
user-mode feature. VB-CABLE is the production virtual-microphone driver and is
installed system-wide with administrator approval. The Soundboard Studio EXE
must remain open while its mixed microphone is in use.
