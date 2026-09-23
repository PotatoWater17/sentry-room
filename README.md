# Sentry Room

Sentry Room is a small Windows app that lets you watch this PC from a phone. Open one page and you get the webcam, the room microphone, a way to talk through the PC speakers, and a set of sounds. It is meant for your own room while you are away. It stays on Tailscale or your home network and is not published to the public internet.

## Features

- Live webcam in the browser, with a clock burned into the picture so you can see that it is current.
- A camera menu. The PC scans connected cameras at startup and labels each one with its resolution.
- Listen to the room through a chosen microphone. While you hold talk, your own phone stops hearing the room so the speakers do not howl back into the call.
- Armed watch. While armed, the camera and microphone stay on. Movement or a loud sound makes the speaker say "recording" and saves a 10 second clip with picture and room audio. Clips open on their own page, with play, pause, seek, speed, and next and previous. The default is 100 clips; you can set that number, and the oldest clips are deleted to stay under it. Motion and sound sensitivity, and a pause between alerts, are adjustable. Your own voice on hold-to-talk does not count as a loud sound.
- Hold to talk from the phone microphone out of a chosen speaker. The space bar does the same from a keyboard.
- A speaker menu. Choosing a speaker plays a short confirmation tone on that device.
- Built-in sounds: doorbell, knock, phone, chime, ding, notify, fanfare, alert, alarm, siren, critical, and unlock. Knock is a real door knock. Alarm and siren keep going until Stop audio.
- Extra `.wav` files dropped in a `sounds` folder show up as buttons after a restart.
- A code you choose on first launch. It is stored as a hash, not as readable text, and it is not printed in the window.
- The phone page can be added to the home screen and opened like an app.
- Port 8787 accepts both `http://` and `https://`. Phones often upgrade the address to https on their own. The phone microphone works on the https page.

## Requirements

- Windows 10 or 11
- [Python 3.11](https://www.python.org/downloads/windows/) from python.org, not the Microsoft Store
- A webcam, a microphone, and a speaker or headphones
- Optional: [Tailscale](https://tailscale.com/download) on this PC and on the phone, signed into the same account, if the phone is not on the same Wi-Fi

During the Python install, leave **Install launcher for all users** checked, and check **Add python.exe to PATH**. The start script looks for the `py` launcher. If that box was missed, run the installer again and choose Modify.

## Run it

1. Download or clone this folder: [github.com/PotatoWater17/sentry-room](https://github.com/PotatoWater17/sentry-room)
2. Double-click **Launch Sentry.bat**. The command window closes. A Sentry Room status window stays open. A Desktop shortcut is created so you can pin it to the taskbar.
3. The first launch creates a `.venv` folder and installs the libraries. A command window may show during that install, then it closes. This can take a few minutes and only happens once.
4. A browser page opens on this PC. The first visit asks you to **create a code** (6 to 64 characters). Type it twice. This code is not printed in the window and it is not saved where someone can read it.
5. On the phone, open the address printed in that window. It looks like `https://this-pc.tailnet.ts.net/`. The browser trusts that address, so it does not say the connection is not private.
6. Sign in with the code, then **choose a camera**. The picture stays off until you do. It also turns off when nobody is watching.
7. Leave the Sentry Room status window open. Closing it turns the camera off. New code in that window replaces the sign-in code without asking for the old one.

If the phone cannot connect, double-click **Allow Firewall.bat** once and approve the prompt. That allows inbound TCP 8787 and 8788. Run it again after you change those ports in `config.json`.

To open Sentry Room whenever this PC signs in, double-click **Install Startup.bat** once. It waits about 20 seconds so Windows can finish starting the camera and speakers, then opens the status window. If Windows signs you in automatically, that happens right after power on. To turn it off, delete `Sentry Room.cmd` from the Startup folder.

On this PC, `http://127.0.0.1:8787/` can talk as well, because the browser treats localhost as a secure page. From a phone, use the https address. The phone microphone does not work on a plain `http://` page.

Same Wi-Fi uses the LAN address printed in the window. Away from home, use the Tailscale address, also printed there. Both need the firewall rule.

To start from a terminal instead of the bat file:

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

If `py -3.11` is missing, `py -3` is enough as long as it is Python 3.11 or newer.

## If it does not start

- The window says to install Python: install Python 3.11 from python.org, then double-click the bat file again.
- `Could not listen on port 8787`: another Sentry Room window is already open, or another program is using that port. Close the extra window, or change `http_port` in `config.json` and run **Allow Firewall.bat** again.
- No picture, or Listen fails: open Windows Settings, then Privacy & security, then Camera and Microphone, and allow desktop apps.
- The camera menu is empty: close other apps that are using the webcam, then restart Sentry Room.
- The speaker menu skipped a device: that device did not stay open in a short test. Pick another speaker. A short tone plays when a choice works.
- Wrong code too many times: wait about a minute, then try again.
- You forgot the code: on that PC, close Sentry Room and delete `secrets.json` in the app folder. Start it again and create a new code. Deleting that file is the only way in without the code, so do not leave the PC unlocked for other people.

Libraries installed into `.venv` on first launch: aiohttp, numpy, opencv-python-headless, sounddevice, cryptography. You do not install these by hand unless the bat file reports that setup failed.

## Address

On your phone open the Tailscale name printed at startup, `https://<this-pc>.<tailnet>.ts.net/`.

That name uses a certificate the phone already trusts. Do not open `https://100.x.x.x:8787/`. That address is signed by this PC, and the phone will say the connection is not private.

The trusted name is turned on with:

```bat
tailscale serve --bg --yes 8787
```

Run `tailscale serve reset` when you want to turn that name off. Talk still has an HTTP fallback if a WebSocket through that address fails.

## Settings

`config.json` is created on the PC the first time the app runs. It does not contain the code. The code lives only as a hash in `secrets.json`, and Windows limits that file to the user who started the app. Neither file is part of the git repo. Edit `config.json`, then restart:

To change the code, type `code` in the Sentry Room window and press Enter, or double-click **Change Code.bat**. Enter the current code, then the new one twice. Characters stay hidden. Phones have to sign in again. The old code is not shown. Factory reset at the bottom of the page still removes the code and asks you to create one in the browser.

| Key | Meaning |
| --- | --- |
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
- `camera.py` — opens only the camera someone selected, then lets it go when nobody is watching
- `audio.py` — speaker mix, room listen, and device probing
- `web/` — the phone page (`index.html`, `app.js`, `style.css`)
- `Launch Sentry.bat` — creates `.venv` if needed, installs libraries, opens the status window
- `status_ui.py` — status window for running state, latency, and a new code (no current code needed)
- `Install Startup.bat` — starts the app when you sign in to Windows
- `Allow Firewall.bat` — inbound TCP rules for the two ports
- `secrets.json` — hashed code, created on the PC, not in git
- `certs/` — self-signed certificate, created on the PC, not in git
- `sounds/` — optional extra `.wav` files, created when you add them

## Continue in another Cursor window

Paste the block below into a new Cursor chat opened on this folder.

```text
This project is Sentry Room. It is a Windows app in this folder. The owner uses it on their own PC, from a phone on Tailscale, to watch the room while they are away. Repo: https://github.com/PotatoWater17/sentry-room

Run it only with Launch Sentry.bat. Do not leave a second copy running, or it holds the speakers and ports 8787 and 8788. The sign-in code is a PBKDF2 hash in secrets.json (gitignored), not plaintext in config.json, and it is never printed. Never commit config.json, secrets.json, certs/, or .venv/. First launch with no hash shows a setup page. The status window New code button replaces the hash without asking for the old code. Change Code.bat still asks for the current code. Forgetting the code means deleting secrets.json on that PC while the app is stopped.

Stack: Python 3.11, aiohttp, OpenCV (CAP_DSHOW, then MSMF), sounddevice callbacks at 48000 Hz, cryptography for a self-signed cert. Page is web/index.html, web/app.js, web/style.css. Blue and white theme.

Behavior that must keep working:
- Port 8787 accepts both HTTP and HTTPS. HybridProtocol peeks the first byte. 0x16 is a TLS ClientHello and is fed to asyncio.sslproto.SSLProtocol with asyncio.protocols._feed_data_to_buffered_proto, because Python 3.11 SSLProtocol is a BufferedProtocol and has no data_received. Plain HTTP is handed to the aiohttp protocol. Port 8788 stays HTTPS-only.
- Phone talk needs a secure context. https://<tailscale-ip>:8787/ is the address to give them. The first visit shows the self-signed certificate warning.
- Viewer count is request.app["stats"]["viewers"]. Do not assign request.app["viewers"] after startup. aiohttp warns if the Application mapping changes after start.
- The camera does not open at startup and does not scan every index. Names come from Windows device names. Open only the index the signed-in user selects. When the last viewer disconnects, release the camera after about 3 seconds. Do not stream /video.mjpg unless that camera thread is already running.
- PIN cookie is sentry_session, SameSite Strict, 12 hour sliding expiry, 8-try lockout. Compare codes with the hash in secrets.json. http://127.0.0.1:8787 and https://host:8787 are different sites, so the phone signs in on the page it actually uses.
- Speakers and mics: probe with a callback stream, sleep 0.2s, require stream.active. Mics also need at least one callback frame. Device keys are "host::raw name". Prefer WASAPI, then DirectSound, then MME, then WDM-KS. Skip mapper, primary sound driver, and steam streaming. Bluetooth names like Headset (@System32\\...;(Name)) become Name. WDM-KS must be opened with a callback. The output stream stays open and can take exclusive control of that device. The mic opens only while someone is listening, except a short open/close when the menu changes.
- While a client is talking, their listen stream is silence.
- Talk is int16 PCM on WebSocket /ws/talk, with POST /api/talk as a fallback. Listen is chunked PCM on /listen.pcm with a 10-byte prelude: SENTRY plus uint32le sample rate.
- Built-in sounds are synthesized when Windows Media wavs are missing. Extra wavs belong in sounds/.
- Do not describe the contents of a test webcam frame.

Startup banner does not print the code. It prints the phone Watch and Talk URLs. Allow Firewall.bat reads the ports from config.json.
```
