"""Sentry Room: watch this PC's camera and use its speakers from a browser."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import hmac
import ipaddress
import json
import secrets
import mimetypes
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

print("Starting Sentry Room...", flush=True)
logging.getLogger("aiohttp.server").setLevel(logging.CRITICAL)
logging.getLogger("aiohttp.web").setLevel(logging.CRITICAL)

from aiohttp import WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import numpy as np

from audio import RATE, AudioEngine, AudioUnavailable, load_sounds, resample
from camera import Camera, write_icon

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
CERT_DIR = ROOT / "certs"
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
mimetypes.add_type("application/manifest+json", ".webmanifest")

DEFAULTS = {
    "pin": "",
    "http_port": 8787,
    "https_port": 8788,
    "camera_index": 0,
    "width": 960,
    "fps": 12,
    "jpeg_quality": 72,
    "mirror": False,
    "bind": "0.0.0.0",
    "speaker": "",
    "microphone": "",
}


def clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def clamp_gain(value, default: float = 1.0, high: float = 2.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(0.0, min(high, number))


def load_config() -> dict:
    config = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                config.update(stored)
        except (OSError, json.JSONDecodeError):
            print("config.json could not be read. A new one will be written.", flush=True)
    pin = str(config.get("pin") or "").strip()
    if not pin:
        pin = f"{secrets.randbelow(1_000_000):06d}"
    config["pin"] = pin
    config["http_port"] = clamp_int(config.get("http_port"), 8787, 1, 65535)
    config["https_port"] = clamp_int(config.get("https_port"), 8788, 1, 65535)
    if config["https_port"] == config["http_port"]:
        config["https_port"] = 8788 if config["http_port"] != 8788 else 8789
    config["camera_index"] = clamp_int(config.get("camera_index"), 0, 0, 10)
    config["width"] = clamp_int(config.get("width"), 960, 320, 1920)
    config["fps"] = clamp_int(config.get("fps"), 12, 2, 30)
    config["jpeg_quality"] = clamp_int(config.get("jpeg_quality"), 72, 40, 95)
    config["mirror"] = bool(config.get("mirror"))
    config["speaker"] = str(config.get("speaker") or "").strip()
    config["microphone"] = str(config.get("microphone") or "").strip()
    bind = str(config.get("bind") or "0.0.0.0").strip()
    config["bind"] = bind or "0.0.0.0"
    save_config(config)
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def hostname() -> str:
    return socket.gethostname() or "sentry-room"


def tailscale_dns() -> str | None:
    candidates = [
        shutil.which("tailscale"),
        r"C:\Program Files\Tailscale\tailscale.exe",
    ]
    exe = next((path for path in candidates if path and Path(path).exists()), None)
    if not exe:
        return None
    try:
        output = subprocess.check_output(
            [exe, "status", "--json"],
            text=True,
            timeout=4,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(output)
        name = str(data.get("Self", {}).get("DNSName", "")).strip().rstrip(".")
        return name or None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, AttributeError):
        return None


def host_ipv4() -> list[str]:
    found: set[str] = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(hostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    ordered = []
    for ip in found:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            continue
        ordered.append(ip)

    def sort_key(ip: str) -> tuple:
        address = ipaddress.ip_address(ip)
        if address.is_loopback:
            group = 3
        elif address in TAILSCALE_NET:
            group = 0
        elif address.is_private:
            group = 1
        else:
            group = 2
        return (group, ip)

    return sorted(set(ordered), key=sort_key)


def link_kind(ip: str) -> str:
    address = ipaddress.ip_address(ip)
    if address.is_loopback:
        return "local"
    if address in TAILSCALE_NET:
        return "tailscale"
    return "lan"


def describe_links(config: dict, dns_name: str | None) -> list[dict]:
    links = []
    if dns_name:
        links.append({
            "label": dns_name,
            "kind": "tailscale",
            "http": f"http://{dns_name}:{config['http_port']}/",
            "https": f"https://{dns_name}:{config['http_port']}/",
        })
    for ip in host_ipv4():
        links.append({
            "label": ip,
            "kind": link_kind(ip),
            "http": f"http://{ip}:{config['http_port']}/",
            "https": f"https://{ip}:{config['http_port']}/",
        })
    return links


def cert_covers(cert_path: Path, ips: set[str], names: set[str]) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        have_names = {name.lower() for name in san.get_values_for_type(x509.DNSName)}
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    return ips <= have_ips and {name.lower() for name in names} <= have_names


def ensure_certificate(ips: list[str], dns_names: list[str]) -> tuple[Path, Path]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = CERT_DIR / "cert.pem"
    key_path = CERT_DIR / "key.pem"
    ip_set = set(ips)
    name_set = set(dns_names)
    if cert_path.exists() and key_path.exists() and cert_covers(cert_path, ip_set, name_set):
        return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0])])
    alt_names: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    alt_names.extend(x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips)
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=800))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def client_ip(request: web.Request) -> str:
    remote = request.remote or "?"
    if remote in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or remote
    return remote


def authed(request: web.Request) -> bool:
    token = request.cookies.get("sentry_session", "")
    return bool(token) and token in request.app["sessions"]


def remember_session(request: web.Request, token: str) -> None:
    sessions: dict = request.app["sessions"]
    sessions[token] = time.time()
    while len(sessions) > 24:
        oldest = min(sessions, key=sessions.get)
        sessions.pop(oldest, None)


def require(request: web.Request) -> None:
    if not authed(request):
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Sign in required"}),
            content_type="application/json",
        )


def json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def nodelay(request: web.Request) -> None:
    sock = request.transport.get_extra_info("socket") if request.transport else None
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


@web.middleware
async def guard(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"Request failed: {exc}", flush=True)
        return json_error(500, "Something went wrong on this PC.")
    if not getattr(response, "prepared", False):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
    return response


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "index.html")


async def info(request: web.Request) -> web.Response:
    config = request.app["config"]
    return web.json_response({
        "name": request.app["hostname"],
        "httpPort": config["http_port"],
        "httpsPort": config["https_port"],
        "links": request.app["links"],
    })


async def me(request: web.Request) -> web.Response:
    if not authed(request):
        return json_error(401, "Sign in required")
    return web.json_response({"ok": True, "name": request.app["hostname"]})


async def login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return json_error(400, "Enter your PIN.")
    ip = client_ip(request)
    now = time.monotonic()
    fails: dict = request.app["fails"]
    count, locked_until = fails.get(ip, (0, 0.0))
    if locked_until > now:
        return json_error(429, "Too many tries. Wait a minute and try again.")
    entered = str(data.get("pin", "")).strip()
    expected = str(request.app["config"]["pin"])
    matches = len(entered) == len(expected) and hmac.compare_digest(entered, expected)
    if not matches:
        count += 1
        locked = now + 60 if count >= 8 else 0.0
        fails[ip] = (0 if locked else count, locked)
        await asyncio.sleep(0.35)
        return json_error(401, "Wrong PIN.")
    fails.pop(ip, None)
    token = secrets.token_urlsafe(32)
    remember_session(request, token)
    response = web.json_response({"ok": True})
    response.set_cookie(
        "sentry_session",
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="Strict",
        secure=request.secure,
        path="/",
    )
    print(f"Signed in from {ip}", flush=True)
    return response


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get("sentry_session", "")
    request.app["sessions"].pop(token, None)
    response = web.json_response({"ok": True})
    response.del_cookie("sentry_session", path="/")
    return response


async def sounds(request: web.Request) -> web.Response:
    require(request)
    audio: AudioEngine = request.app["audio"]
    return web.json_response({"sounds": [sound.public() for sound in audio.catalog]})


async def status(request: web.Request) -> web.Response:
    require(request)
    camera: Camera = request.app["camera"]
    audio: AudioEngine = request.app["audio"]
    cam = camera.snapshot()
    age = cam["age"]
    payload = audio.status()
    payload["camera"] = {
        "ok": cam["error"] is None and age is not None and age < 3,
        "error": cam["error"],
        "index": cam["index"],
        "width": cam["width"],
        "height": cam["height"],
    }
    payload["cameras"] = cam["devices"]
    payload["speakers"] = audio.output_choices()
    payload["microphones"] = audio.input_choices()
    payload["viewers"] = request.app["stats"]["viewers"]
    return web.json_response(payload)


async def play(request: web.Request) -> web.Response:
    require(request)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return json_error(400, "Choose a sound.")
    audio: AudioEngine = request.app["audio"]
    sound_id = str(data.get("id", ""))
    volume = clamp_gain(data.get("volume"), 0.85, 1.5)
    sound = audio.sounds.get(sound_id)
    if sound is None:
        return json_error(404, "That sound is not on this PC.")
    try:
        started = audio.play(sound_id, volume, bool(data.get("loop")) and sound.loop)
    except AudioUnavailable as exc:
        return json_error(503, str(exc))
    if started is None:
        return json_error(404, "That sound is not on this PC.")
    return web.json_response({"ok": True, "looping": audio.status()["looping"]})


async def stop_audio(request: web.Request) -> web.Response:
    require(request)
    request.app["audio"].stop()
    return web.json_response({"ok": True})


async def switch_camera(request: web.Request) -> web.Response:
    require(request)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        data = {}
    camera: Camera = request.app["camera"]
    if "index" in data:
        index = clamp_int(data.get("index"), camera.index, 0, 10)
    else:
        index = (camera.index + 1) % 4
    camera.set_index(index)
    config = request.app["config"]
    config["camera_index"] = index
    await asyncio.to_thread(save_config, config)
    return web.json_response({"ok": True, "index": index})


async def select_speaker(request: web.Request) -> web.Response:
    require(request)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        data = {}
    audio: AudioEngine = request.app["audio"]
    key = str(data.get("key", "")).strip()
    if not key:
        return json_error(400, "Choose a speaker.")
    try:
        name = await asyncio.to_thread(audio.use_output, key)
    except AudioUnavailable as exc:
        return json_error(503, str(exc))
    config = request.app["config"]
    config["speaker"] = audio.output_key
    await asyncio.to_thread(save_config, config)
    return web.json_response({"ok": True, "name": name, "key": audio.output_key})


async def select_microphone(request: web.Request) -> web.Response:
    require(request)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        data = {}
    audio: AudioEngine = request.app["audio"]
    key = str(data.get("key", "")).strip()
    if not key:
        return json_error(400, "Choose a microphone.")
    try:
        name = await asyncio.to_thread(audio.use_input, key)
    except AudioUnavailable as exc:
        return json_error(503, str(exc))
    config = request.app["config"]
    config["microphone"] = audio.input_key
    await asyncio.to_thread(save_config, config)
    return web.json_response({"ok": True, "name": name, "key": audio.input_key})


async def video(request: web.Request) -> web.StreamResponse:
    require(request)
    nodelay(request)
    camera: Camera = request.app["camera"]
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
    await response.prepare(request)
    request.app["stats"]["viewers"] += 1
    print(f"Camera viewer connected from {client_ip(request)}", flush=True)
    interval = 1 / max(2, request.app["config"]["fps"])
    try:
        while True:
            jpeg = camera.latest_jpeg()
            if not jpeg:
                await asyncio.sleep(interval)
                continue
            packet = (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode("ascii")
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            await response.write(packet)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, RuntimeError):
        return response
    finally:
        stats = request.app["stats"]
        stats["viewers"] = max(0, stats["viewers"] - 1)
    return response


def _pcm_from_bytes(raw: bytes) -> np.ndarray:
    if len(raw) % 2:
        raw = raw[:-1]
    if len(raw) < 2:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


async def talk_chunk(request: web.Request) -> web.Response:
    require(request)
    audio: AudioEngine = request.app["audio"]
    rate = clamp_int(request.headers.get("X-Audio-Rate"), 48000, 8000, 96000)
    gain = clamp_gain(request.headers.get("X-Gain"), 1.0, 2.0)
    client_id = request.headers.get("X-Client", "")[:64]
    raw = await request.read()
    if len(raw) > 240000:
        raw = raw[:240000]
    samples = _pcm_from_bytes(raw)
    if samples.size == 0:
        return web.Response(status=204)
    try:
        audio.ensure_output()
    except AudioUnavailable as exc:
        return json_error(503, str(exc))
    audio.mark_talking(client_id)
    audio.set_talk_gain(gain)
    audio.add_talk(resample(samples, rate, RATE))
    return web.Response(status=204)


async def talk_socket(request: web.Request) -> web.WebSocketResponse:
    require(request)
    nodelay(request)
    audio: AudioEngine = request.app["audio"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    client_id = request.query.get("cid", "")[:64]
    rate = 48000
    try:
        audio.ensure_output()
    except AudioUnavailable as exc:
        await ws.send_str(json.dumps({"error": str(exc)}))
        await ws.close()
        return ws
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            kind = data.get("t")
            if kind == "hello":
                rate = clamp_int(data.get("rate"), 48000, 8000, 96000)
                audio.set_talk_gain(clamp_gain(data.get("gain"), 1.0, 2.0))
                audio.mark_talking(client_id)
            elif kind == "gain":
                audio.set_talk_gain(clamp_gain(data.get("gain"), 1.0, 2.0))
            elif kind == "bye":
                audio.clear_talking(client_id)
        elif msg.type == WSMsgType.BINARY:
            raw = msg.data
            if len(raw) > 240000:
                raw = raw[:240000]
            samples = _pcm_from_bytes(raw)
            if samples.size:
                audio.mark_talking(client_id)
                audio.add_talk(resample(samples, rate, RATE))
        elif msg.type in {WSMsgType.CLOSE, WSMsgType.ERROR, WSMsgType.CLOSING}:
            break
    audio.clear_talking(client_id)
    return ws


async def listen(request: web.Request) -> web.StreamResponse:
    require(request)
    nodelay(request)
    audio: AudioEngine = request.app["audio"]
    try:
        rate = audio.ensure_input()
    except AudioUnavailable as exc:
        return json_error(503, str(exc))
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Cache-Control": "no-store",
            "X-Audio-Rate": str(rate),
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    client_id = request.query.get("cid", "")[:64]
    audio.add_listener(queue, client_id)
    try:
        await response.write(b"SENTRY" + struct.pack("<I", int(rate)))
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                transport = request.transport
                if transport is None or transport.is_closing():
                    break
                stream = audio.in_stream
                if stream is None or not getattr(stream, "active", False):
                    break
                continue
            if audio.is_talking(client_id):
                chunk = bytes(len(chunk))
            await response.write(chunk)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, RuntimeError):
        return response
    finally:
        audio.remove_listener(queue)
    return response


def print_banner(config: dict, links: list[dict], speaker: str) -> None:
    print("", flush=True)
    print("=" * 62, flush=True)
    print("  SENTRY ROOM", flush=True)
    print("  Leave this window open. Closing it turns the camera off.", flush=True)
    print("=" * 62, flush=True)
    print(f"  PIN: {config['pin']}", flush=True)
    print(f"  Speakers: {speaker}", flush=True)
    print("", flush=True)
    print("  On this PC:", flush=True)
    print(f"    http://127.0.0.1:{config['http_port']}/", flush=True)
    phone = [link for link in links if link["kind"] != "local"]
    if phone:
        print("", flush=True)
        print("  On your phone, over Tailscale or the same network:", flush=True)
        for link in phone:
            print(f"    {link['label']}", flush=True)
            print(f"      Watch:  {link['http']}", flush=True)
            print(f"      Talk:   {link['https']}", flush=True)
    print("", flush=True)
    print("  The https address is the same port. Continue past the certificate", flush=True)
    print("  warning once so the phone microphone can be used.", flush=True)
    print("  If the phone cannot connect, run Allow Firewall.bat once.", flush=True)
    print("=" * 62, flush=True)
    print("", flush=True)


class HybridProtocol(asyncio.Protocol):
    """Serve HTTP or HTTPS on one port. Phones often open the https address."""

    def __init__(self, factory, ssl_context: ssl.SSLContext | None) -> None:
        self.factory = factory
        self.ssl_context = ssl_context
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport
        sock = transport.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

    def data_received(self, data: bytes) -> None:
        transport = self.transport
        if transport is None or not data:
            return
        transport.pause_reading()
        loop = asyncio.get_running_loop()
        if data[0] == 0x16 and self.ssl_context is not None:
            app_protocol = self.factory()
            secured = asyncio.sslproto.SSLProtocol(
                loop,
                app_protocol,
                self.ssl_context,
                None,
                server_side=True,
            )
            transport.set_protocol(secured)
            secured.connection_made(transport)
            asyncio.protocols._feed_data_to_buffered_proto(secured, data)
            transport.resume_reading()
            return
        app_protocol = self.factory()
        transport.set_protocol(app_protocol)
        app_protocol.connection_made(transport)
        transport.resume_reading()
        app_protocol.data_received(data)

    def connection_lost(self, exc) -> None:
        return None


class HybridSite:
    def __init__(self, runner: web.AppRunner, host: str, port: int, ssl_context: ssl.SSLContext) -> None:
        self.runner = runner
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.server = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        factory = self.runner.server
        context = self.ssl_context

        def protocol_factory():
            return HybridProtocol(factory, context)

        self.server = await loop.create_server(protocol_factory, self.host, self.port, backlog=128)

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None


def open_local(port: int) -> None:
    def _open() -> None:
        time.sleep(0.8)
        webbrowser.open(f"http://127.0.0.1:{port}/")

    threading.Thread(target=_open, daemon=True).start()


async def serve() -> None:
    config = load_config()
    dns_name = tailscale_dns()
    names = ["localhost", hostname()]
    if dns_name:
        names.append(dns_name)
    ips = host_ipv4()
    links = describe_links(config, dns_name)
    sound_list = load_sounds(ROOT / "sounds")
    audio = AudioEngine(sound_list, config["speaker"], config["microphone"])
    try:
        audio.ensure_output()
        speaker = audio.output_name or "ready"
    except AudioUnavailable as exc:
        speaker = str(exc)
        print(speaker, flush=True)
    if audio.output_key:
        config["speaker"] = audio.output_key
    if not config.get("microphone"):
        mics = audio.input_choices()
        if mics:
            audio.input_key = mics[0]["key"]
            config["microphone"] = audio.input_key
    save_config(config)

    def remember_devices() -> None:
        audio.refresh_devices()
        if audio.output_key:
            config["speaker"] = audio.output_key
        if audio.input_key:
            config["microphone"] = audio.input_key
        save_config(config)

    threading.Thread(target=remember_devices, name="sentry-devices", daemon=True).start()
    camera = Camera(
        index=config["camera_index"],
        width=config["width"],
        fps=config["fps"],
        quality=config["jpeg_quality"],
        mirror=config["mirror"],
    )
    try:
        write_icon(WEB / "icon.png")
    except Exception as exc:
        print(f"App icon skipped: {exc}", flush=True)
    audio.attach_loop(asyncio.get_running_loop())

    app = web.Application(middlewares=[guard], client_max_size=1024 * 1024)
    app["config"] = config
    app["audio"] = audio
    app["camera"] = camera
    app["sessions"] = {}
    app["fails"] = {}
    app["stats"] = {"viewers": 0}
    app["hostname"] = hostname()
    app["links"] = links
    app.router.add_get("/", index)
    app.router.add_get("/api/info", info)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/sounds", sounds)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/play", play)
    app.router.add_post("/api/stop", stop_audio)
    app.router.add_post("/api/camera", switch_camera)
    app.router.add_post("/api/speaker", select_speaker)
    app.router.add_post("/api/microphone", select_microphone)
    app.router.add_post("/api/talk", talk_chunk)
    app.router.add_get("/ws/talk", talk_socket)
    app.router.add_get("/listen.pcm", listen)
    app.router.add_get("/video.mjpg", video)
    app.router.add_static("/static/", WEB, show_index=False)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    https_ready = False
    hybrid = None
    try:
        cert_path, key_path = ensure_certificate(ips, names)
        shared_ssl = ssl_context(cert_path, key_path)
        hybrid = HybridSite(runner, config["bind"], config["http_port"], shared_ssl)
        await hybrid.start()
        https_ready = True
    except OSError as exc:
        print(f"Could not listen on port {config['http_port']}: {exc}", flush=True)
        print("Change http_port in config.json, or close the program using that port.", flush=True)
        camera.stop()
        audio.close()
        await runner.cleanup()
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"Could not start the page: {exc}", flush=True)
        camera.stop()
        audio.close()
        await runner.cleanup()
        raise SystemExit(1) from exc

    try:
        https_site = web.TCPSite(
            runner,
            config["bind"],
            config["https_port"],
            ssl_context=shared_ssl,
        )
        await https_site.start()
    except OSError as exc:
        print(f"Extra secure port {config['https_port']} is unavailable: {exc}", flush=True)
    except Exception as exc:
        print(f"Extra secure port was not started: {exc}", flush=True)

    print_banner(config, links, speaker)
    if not https_ready:
        print("The https talk address is not running yet.", flush=True)
    if "--no-browser" not in sys.argv:
        open_local(config["http_port"])
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        if hybrid is not None:
            await hybrid.stop()
        camera.stop()
        audio.close()
        await runner.cleanup()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        print("\nSentry Room stopped.", flush=True)


if __name__ == "__main__":
    main()
