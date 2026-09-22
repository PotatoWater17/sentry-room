# Sentry Room

Sentry Room is a small Windows app that lets you watch this PC from a phone. Open one page and you get the webcam, the room microphone, a way to talk through the PC speakers, and a set of sounds. It is meant for your own room while you are away. It stays on Tailscale or your home network and is not published to the public internet.

## Features

- Live webcam in the browser, with a clock burned into the picture so you can see that it is current.
- A camera menu. The PC scans connected cameras at startup and labels each one with its resolution.
- Listen to the room through a chosen microphone. While you hold talk, your own phone stops hearing the room so the speakers do not howl back into the call.
- Hold to talk from the phone microphone out of a chosen speaker. The space bar does the same from a keyboard.
- A speaker menu. Choosing a speaker plays a short confirmation tone on that device.
- Built-in sounds: doorbell, knock, phone, chime, ding, notify, fanfare, alert, alarm, siren, critical, and unlock. Alarm and siren keep going until Stop audio.
- Extra `.wav` files dropped in a `sounds` folder show up as buttons after a restart.
- PIN sign-in with a short lockout after repeated wrong tries. The PIN is printed when the app starts and saved in `config.json` on the PC.
- The phone page can be added to the home screen and opened like an app.
- Port 8787 accepts both `http://` and `https://`. Phones often upgrade the address to https on their own. The phone microphone works on the https page.

## Requirements

- Windows 10 or 11
- Python 3.11 (the start script also accepts a generic `py -3` if 3.11 is missing)
- A webcam and a microphone that Windows allows desktop apps to use
- A speaker or headphone device
- A phone on the same Tailscale network, or on the same home network
- The first launch installs the Python libraries. That can take a few minutes.

Python packages, installed into `.venv` by the start script:

- aiohttp
- numpy
- opencv-python-headless
- sounddevice
- cryptography

## Start

1. Double-click **Start Sentry Room.bat**.
2. Leave that window open. Closing it turns the camera off.
3. Read the PIN in that window.
4. A browser page opens on this PC. On your phone, use the Tailscale address printed in the window.

If the phone cannot connect, double-click **Allow Firewall.bat** once and approve the prompt. That opens inbound TCP 8787 and 8788.

If the picture or room audio fails, open Windows Settings, then Privacy & security, and allow desktop apps to use the Camera and the Microphone.

## Address

On your phone open the **Talk** line printed at startup. It looks like:

`https://<tailscale-ip>:8787/`

The same port also accepts `http://<tailscale-ip>:8787/` for watching and sounds. The first https visit shows a certificate warning because this PC signed the certificate itself. Continue once, then sign in with the PIN.

On this PC, `http://127.0.0.1:8787/` can talk as well, because the browser treats localhost as a secure page.

Port 8788 is a second https address for an old bookmark. The phone should use 8787.

If you would rather skip the certificate warning, with the app already running:

```bat
tailscale serve --bg --https=443 http://127.0.0.1:8787
```

Then open the `https://<this-pc>.<tailnet>.ts.net` address Tailscale prints. That certificate is trusted inside your tailnet. Run `tailscale serve reset` when you want to turn that off. Talk still has an HTTP fallback if a WebSocket through Tailscale Serve fails.

## Settings

`config.json` is created on the PC the first time the app runs. It is not part of the git repo, because it holds the PIN. Edit it, then restart:

| Key | Meaning |
| --- | --- |
| `pin` | Sign-in PIN |
| `http_port` | Main page port. Default `8787`. Speaks HTTP and HTTPS. |
| `https_port` | Extra HTTPS-only port. Default `8788`. |
| `camera_index` | Webcam index. Also chosen from the page. |
| `speaker` | Output device key, `host::name`. Also chosen from the page. |
| `microphone` | Input device key, `host::name`. Also chosen from the page. |
| `width` | Frame width sent to the phone. Default `960`. |
| `fps` | Frame rate. Default `12`. |
| `jpeg_quality` | JPEG quality. Default `72`. |
| `mirror` | `true` flips the picture. |
| `bind` | Listen address. Default `0.0.0.0`, so Tailscale and local addresses both work. |

## Project layout

- `app.py` — web server, PIN, routes, hybrid HTTP/HTTPS listener
- `camera.py` — webcam open, scan, and MJPEG frames
- `audio.py` — speaker mix, room listen, and device probing
- `web/` — the phone page (`index.html`, `app.js`, `style.css`)
- `Start Sentry Room.bat` — creates `.venv` if needed, installs libraries, runs the app
- `Allow Firewall.bat` — inbound TCP rules for the two ports
- `certs/` — self-signed certificate, created on the PC, not in git
- `sounds/` — optional extra `.wav` files, created when you add them

## Continue in another Cursor window

Paste the block below into a new Cursor chat opened on this folder.

```text
This project is Sentry Room. It is a Windows app in this folder. The owner uses it on their own PC, from a phone on Tailscale, to watch the room while they are away. Repo: https://github.com/PotatoWater17/sentry-room

Run it only with Start Sentry Room.bat. Do not leave a second app.py running, or it holds the webcam, the speakers, and ports 8787 and 8788. The PIN lives in config.json (gitignored) and is printed at startup. Never commit config.json, certs/, or .venv/.

Stack: Python 3.11, aiohttp, OpenCV (CAP_DSHOW, then MSMF), sounddevice callbacks at 48000 Hz, cryptography for a self-signed cert. Page is web/index.html, web/app.js, web/style.css. Olive and amber night theme.

Behavior that must keep working:
- Port 8787 accepts both HTTP and HTTPS. HybridProtocol peeks the first byte. 0x16 is a TLS ClientHello and is fed to asyncio.sslproto.SSLProtocol with asyncio.protocols._feed_data_to_buffered_proto, because Python 3.11 SSLProtocol is a BufferedProtocol and has no data_received. Plain HTTP is handed to the aiohttp protocol. Port 8788 stays HTTPS-only.
- Phone talk needs a secure context. https://<tailscale-ip>:8787/ is the address to give them. The first visit shows the self-signed certificate warning.
- Viewer count is request.app["stats"]["viewers"]. Do not assign request.app["viewers"] after startup. aiohttp warns if the Application mapping changes after start.
- Cameras: after the first good frame, probe indexes 0-5 with CAP_DSHOW and label them "Camera N (WxH)". OpenCV log level is ERROR so empty-index DSHOW warnings stay quiet.
- Speakers and mics: probe with a callback stream, sleep 0.2s, require stream.active. Mics also need at least one callback frame. Device keys are "host::raw name". Prefer WASAPI, then DirectSound, then MME, then WDM-KS. Skip mapper, primary sound driver, and steam streaming. Bluetooth names like Headset (@System32\\...;(Name)) become Name. WDM-KS must be opened with a callback. The output stream stays open and can take exclusive control of that device. The mic opens only while someone is listening, except a short open/close when the menu changes.
- While a client is talking, their listen stream is silence.
- Talk is int16 PCM on WebSocket /ws/talk, with POST /api/talk as a fallback. Listen is chunked PCM on /listen.pcm with a 10-byte prelude: SENTRY plus uint32le sample rate.
- PIN cookie is sentry_session, SameSite Strict, with an 8-try lockout. http://127.0.0.1:8787 and https://host:8787 are different sites, so the phone signs in on the page it actually uses.
- Built-in sounds are synthesized when Windows Media wavs are missing. Extra wavs belong in sounds/.
- Do not describe the contents of a test webcam frame.

Startup banner prints the PIN and the phone Watch and Talk URLs. Allow Firewall.bat reads the ports from config.json.
```
