# Privacy Policy

Soundboard Studio does not include analytics, telemetry, advertising, user
accounts, or a developer-operated cloud service. The project maintainers do
not receive pad labels, sound files, images, microphone audio, device names, or
usage history.

## Data Stored On The Computer

Profiles, settings, copied pad assets, diagnostics, downloaded helpers, and the
optional VB-CABLE installer are stored beside the portable executable. They
remain under the user's control and are not sent to the project maintainers.

## Local Remote Mode

When the phone remote is running in Local mode, the app starts an HTTP and
WebSocket server on the computer. Devices on the same local network can connect
to the displayed address. The remote receives pad labels and images and sends
control actions. Microphone and soundboard audio are not streamed to the phone.

## Internet Remote Mode

Internet mode is disabled until the user selects it. When enabled, the app:

- downloads the official open-source `cloudflared` executable from GitHub if it
  is not already available;
- starts a temporary Cloudflare Quick Tunnel; and
- sends the remote page, pad labels, pad images, and control messages through
  Cloudflare between the computer and the user's browser.

The app uses a private QR token and browser cookie to restrict the temporary
remote. Audio is still mixed locally and is not sent through the tunnel. Use of
the tunnel is also subject to Cloudflare's privacy terms.

## Virtual Microphone Setup

If VB-CABLE is missing and installation is requested, the app downloads the
official installer from VB-Audio. Installation requires Windows UAC and is
governed by VB-Audio's terms. No installation information is returned to the
Soundboard Studio maintainers.

## Removing Data

See [Uninstalling Soundboard Studio](docs/UNINSTALL.md) for complete removal
instructions.
