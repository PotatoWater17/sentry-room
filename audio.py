"""Speaker playback, room microphone, and built-in sounds."""

from __future__ import annotations

import asyncio
import re
import time
import wave
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - missing PortAudio
    sd = None

RATE = 48000
WINDOWS_MEDIA = Path(r"C:\Windows\Media")


class AudioUnavailable(Exception):
    pass


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size == 0 or src_rate == dst_rate:
        return samples
    count = max(1, int(round(samples.size * float(dst_rate) / float(src_rate))))
    old_x = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    new_x = np.linspace(0.0, 1.0, count, endpoint=False)
    return np.interp(new_x, old_x, samples).astype(np.float32)


def _fade(samples: np.ndarray, ms: float = 8) -> np.ndarray:
    count = min(samples.size // 2, int(RATE * ms / 1000))
    if count <= 0:
        return samples
    ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)
    out = samples.copy()
    out[:count] *= ramp
    out[-count:] *= ramp[::-1]
    return out


def _normalize(samples: np.ndarray) -> np.ndarray:
    if samples.size == 0:
        return samples.astype(np.float32)
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-4:
        return samples.astype(np.float32)
    if peak < 0.2:
        samples = samples * (0.7 / peak)
    elif peak > 0.9:
        samples = samples * (0.9 / peak)
    return samples.astype(np.float32)


def _tone(freq: float, dur: float, decay: float = 3.5, amp: float = 0.45, harmonics: int = 1) -> np.ndarray:
    count = max(1, int(RATE * dur))
    t = np.arange(count, dtype=np.float32) / RATE
    wave_data = np.zeros(count, dtype=np.float32)
    for partial in range(1, harmonics + 1):
        wave_data += (amp / partial) * np.sin(2 * np.pi * freq * partial * t)
    return (wave_data * np.exp(-decay * t)).astype(np.float32)


def _silence(dur: float) -> np.ndarray:
    return np.zeros(max(1, int(RATE * dur)), dtype=np.float32)


def _mix(parts: list[np.ndarray]) -> np.ndarray:
    length = max(part.size for part in parts)
    mixed = np.zeros(length, dtype=np.float32)
    for part in parts:
        mixed[: part.size] += part
    return _normalize(mixed)


def _knock_hit(seed: int) -> np.ndarray:
    count = int(RATE * 0.16)
    rng = np.random.default_rng(seed)
    t = np.arange(count, dtype=np.float32) / RATE
    noise = rng.uniform(-1, 1, count).astype(np.float32)
    env = np.exp(-32 * t)
    thump = np.sin(2 * np.pi * 85 * t) * np.exp(-18 * t)
    return (0.45 * noise * env + 0.85 * thump).astype(np.float32)


SYNTH = {
    "doorbell": lambda: np.concatenate([_tone(880, 0.32, 6, 0.5, 2), _silence(0.05), _tone(659, 0.85, 3.0, 0.5, 2)]),
    "knock": lambda: np.concatenate([_knock_hit(1), _silence(0.18), _knock_hit(2)]),
    "phone": lambda: np.concatenate([
        0.32 * (np.sin(2 * np.pi * 440 * np.arange(int(RATE * 0.45)) / RATE) + np.sin(2 * np.pi * 480 * np.arange(int(RATE * 0.45)) / RATE)),
        _silence(0.18),
        0.32 * (np.sin(2 * np.pi * 440 * np.arange(int(RATE * 0.45)) / RATE) + np.sin(2 * np.pi * 480 * np.arange(int(RATE * 0.45)) / RATE)),
    ]).astype(np.float32),
    "chime": lambda: _mix([
        np.concatenate([_tone(523.25, 0.7, 2.8, 0.4, 2), _silence(0.9)]),
        np.concatenate([_silence(0.16), _tone(659.25, 0.7, 2.8, 0.38, 2)]),
        np.concatenate([_silence(0.32), _tone(783.99, 0.8, 2.4, 0.36, 2)]),
        np.concatenate([_silence(0.48), _tone(1046.5, 1.0, 2.2, 0.34, 2)]),
    ]),
    "ding": lambda: _tone(1174, 0.4, 8, 0.5, 3),
    "notify": lambda: np.concatenate([_tone(880, 0.12, 12, 0.45, 2), _silence(0.04), _tone(1318, 0.24, 6, 0.42, 2)]),
    "fanfare": lambda: _mix([_tone(523, 0.9, 2.2, 0.32), _tone(659, 0.9, 2.2, 0.3), _tone(784, 1.05, 2.0, 0.3)]),
    "alert": lambda: np.concatenate([
        _tone(740, 0.15, 10, 0.5), _silence(0.05),
        _tone(740, 0.15, 10, 0.5), _silence(0.05),
        _tone(587, 0.28, 6, 0.5),
    ]),
    "alarm": lambda: np.concatenate([
        piece
        for _ in range(4)
        for piece in (_tone(988, 0.16, 8, 0.55), _silence(0.07), _tone(784, 0.16, 8, 0.55), _silence(0.16))
    ]),
    "siren": lambda: _siren(),
    "critical": lambda: np.concatenate([_tone(196, 0.32, 4, 0.6, 3), _silence(0.04), _tone(146, 0.6, 2.6, 0.6, 3)]),
    "unlock": lambda: np.concatenate([_tone(523, 0.09, 14, 0.42, 2), _silence(0.03), _tone(784, 0.2, 6, 0.42, 2)]),
}


def _siren() -> np.ndarray:
    count = int(RATE * 3.0)
    t = np.arange(count, dtype=np.float32) / RATE
    freq = 640 + 360 * np.sin(2 * np.pi * 0.7 * t)
    phase = np.cumsum(2 * np.pi * freq / RATE).astype(np.float32)
    return _fade(0.36 * np.sin(phase).astype(np.float32), 20)


BUILTIN_SOUNDS = [
    {"id": "doorbell", "label": "Doorbell", "loop": False, "file": None, "synth": "doorbell"},
    # sounds/knock.wav is the last three hits from BigSoundBank 0015, CC0, Joseph Sardin.
    {"id": "knock", "label": "Knock", "loop": False, "file": "knock.wav", "bundled": True, "synth": "knock"},
    # sounds/recording.wav is a spoken "Recording" announcement played when a clip starts.
    {"id": "recording", "label": "Recording", "loop": False, "file": "recording.wav", "bundled": True, "synth": "notify"},
    {"id": "phone", "label": "Phone", "loop": False, "file": "Ring01.wav", "synth": "phone"},
    {"id": "chime", "label": "Chime", "loop": False, "file": "chimes.wav", "synth": "chime"},
    {"id": "ding", "label": "Ding", "loop": False, "file": "ding.wav", "synth": "ding"},
    {"id": "notify", "label": "Notify", "loop": False, "file": "Windows Notify.wav", "synth": "notify"},
    {"id": "fanfare", "label": "Fanfare", "loop": False, "file": "tada.wav", "synth": "fanfare"},
    {"id": "alert", "label": "Alert", "loop": False, "file": "Windows Exclamation.wav", "synth": "alert"},
    {"id": "alarm", "label": "Alarm", "loop": True, "file": "Alarm01.wav", "synth": "alarm"},
    {"id": "siren", "label": "Siren", "loop": True, "file": None, "synth": "siren"},
    {"id": "critical", "label": "Critical", "loop": False, "file": "Windows Critical Stop.wav", "synth": "critical"},
    {"id": "unlock", "label": "Unlock", "loop": False, "file": "Windows Unlock.wav", "synth": "unlock"},
]


def load_wav(path: Path, fade_ms: float = 8) -> np.ndarray | None:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, OSError):
        return None
    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        return None
    if channels > 1:
        usable = data.size - (data.size % channels)
        data = data[:usable].reshape(-1, channels).mean(axis=1)
    if rate <= 0 or data.size == 0:
        return None
    return _fade(_normalize(resample(data, rate, RATE)), fade_ms)


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "sound"


class Sound:
    def __init__(self, sound_id: str, label: str, loop: bool, samples: np.ndarray, kind: str) -> None:
        self.id = sound_id
        self.label = label
        self.loop = loop
        self.samples = np.ascontiguousarray(samples, dtype=np.float32)
        self.kind = kind

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "loop": self.loop, "kind": self.kind}


def load_sounds(custom_dir: Path) -> list[Sound]:
    sounds: list[Sound] = []
    seen: set[str] = set()
    for item in BUILTIN_SOUNDS:
        samples = None
        kind = "synthesized"
        if item["file"]:
            source = custom_dir / item["file"] if item.get("bundled") else WINDOWS_MEDIA / item["file"]
            loaded = load_wav(source, 2 if item.get("bundled") else 8)
            if loaded is not None and loaded.size:
                samples = loaded
                kind = "recorded" if item.get("bundled") else "windows"
        if samples is None:
            samples = _fade(_normalize(np.asarray(SYNTH[item["synth"]](), dtype=np.float32)))
        sounds.append(Sound(item["id"], item["label"], item["loop"], samples, kind))
        seen.add(item["id"])

    custom_dir.mkdir(parents=True, exist_ok=True)
    bundled_names = {str(item["file"]).lower() for item in BUILTIN_SOUNDS if item.get("bundled") and item.get("file")}
    for path in sorted(custom_dir.glob("*.wav")):
        if path.name.lower() in bundled_names:
            continue
        loaded = load_wav(path)
        if loaded is None or loaded.size == 0:
            continue
        sound_id = _slug(path.stem)
        base = sound_id
        suffix = 2
        while sound_id in seen:
            sound_id = f"{base}-{suffix}"
            suffix += 1
        seen.add(sound_id)
        label = path.stem.replace("_", " ").replace("-", " ")
        sounds.append(Sound(sound_id, label, False, loaded, "custom"))
    return sounds


def friendly_name(raw: str) -> str:
    text = " ".join(str(raw).replace("\r", " ").replace("\n", " ").split())
    text = re.sub(r"\(\s*\)", "", text).strip()
    match = re.search(r";\(([^)]+)\)", text)
    if match and ("@System32" in text or "%" in text):
        return match.group(1).strip()
    return text


def _host_name(hostapi: int) -> str:
    return str(sd.query_hostapis(int(hostapi))["name"])


def _host_rank(host: str) -> int:
    low = host.lower()
    if "wasapi" in low:
        return 0
    if "directsound" in low:
        return 1
    if "mme" in low:
        return 2
    if "wdm" in low:
        return 3
    return 9


def device_key(index: int) -> str:
    info = sd.query_devices(index)
    return f"{_host_name(int(info['hostapi']))}::{info['name']}"


def _skipped(name: str) -> bool:
    low = name.lower()
    return any(token in low for token in ("mapper", "primary sound driver", "steam streaming"))


def _audio_sort(raw: str, kind: str) -> tuple:
    low = raw.lower()
    label = friendly_name(raw).lower()
    if "remote audio" in low:
        group = 9
    elif kind == "output":
        if "realtek" in low and "speaker" in low:
            group = 0
        elif "speaker" in low:
            group = 1
        elif "headphone" in low or "headset" in low or "earphone" in low:
            group = 2
        else:
            group = 3
    elif "camera" in low:
        group = 0
    elif "microphone" in low or "mic" in low:
        group = 1
    else:
        group = 2
    return (group, label)


def list_audio_devices(kind: str) -> list[dict]:
    if sd is None:
        return []
    want_output = kind == "output"
    grouped: dict[str, dict] = {}
    for index, device in enumerate(sd.query_devices()):
        channels = int(device["max_output_channels"] if want_output else device["max_input_channels"])
        if channels < 1:
            continue
        raw = str(device["name"])
        if _skipped(raw):
            continue
        host = _host_name(int(device["hostapi"]))
        label = friendly_name(raw)
        current = grouped.get(label.lower())
        rank = _host_rank(host)
        if current is not None and rank >= current["rank"]:
            continue
        grouped[label.lower()] = {
            "key": f"{host}::{raw}",
            "label": label,
            "index": index,
            "rank": rank,
            "sort": _audio_sort(raw, kind),
        }
    choices = sorted(grouped.values(), key=lambda item: item["sort"])
    for item in choices:
        item.pop("rank", None)
        item.pop("sort", None)
        item.pop("index", None)
    return choices


def _try_device(kind: str, index: int) -> bool:
    info = sd.query_devices(index)
    channels = int(info["max_output_channels"] if kind == "output" else info["max_input_channels"])
    if channels < 1:
        return False
    use_channels = 2 if kind == "output" and channels >= 2 else 1
    native = int(info.get("default_samplerate") or 0)
    rates = []
    for rate in (RATE if kind == "output" else native, native, 48000, 44100, 16000):
        if rate and rate not in rates:
            rates.append(int(rate))

    for rate in rates:
        got = {"n": 0}

        def callback(data, frames, time_info, status, _got=got):
            _got["n"] += int(frames)
            if kind == "output":
                data.fill(0)

        stream = None
        try:
            if kind == "output":
                stream = sd.OutputStream(
                    device=index,
                    samplerate=rate,
                    channels=use_channels,
                    dtype="float32",
                    callback=callback,
                    blocksize=1024,
                )
            else:
                stream = sd.InputStream(
                    device=index,
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    callback=callback,
                )
            stream.start()
            time.sleep(0.2)
            working = bool(stream.active) and (kind == "output" or got["n"] > 0)
            stream.stop()
            stream.close()
            if working:
                return True
        except Exception:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
    return False


def resolve_device(key: str, kind: str) -> int | None:
    if sd is None or not key:
        return None
    want_output = kind == "output"
    host, sep, name = key.partition("::")
    matches: list[tuple[int, int]] = []
    for index, device in enumerate(sd.query_devices()):
        channels = int(device["max_output_channels"] if want_output else device["max_input_channels"])
        if channels < 1:
            continue
        raw = str(device["name"])
        this_host = _host_name(int(device["hostapi"]))
        if sep and this_host == host and raw == name:
            return index
        if key.lower() in raw.lower() or key.lower() in friendly_name(raw).lower():
            matches.append((_host_rank(this_host), index))
    if not matches:
        return None
    matches.sort()
    return matches[0][1]


def _output_candidates(hint: str) -> list[int]:
    ranked: list[tuple[int, int]] = []
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_output_channels"]) < 1:
            continue
        name = str(device["name"])
        low = name.lower()
        if hint:
            if hint not in low:
                continue
            ranked.append((0, index))
            continue
        if any(skip in low for skip in ("remote audio", "steam", "mapper", "primary sound")):
            continue
        if "realtek" in low and "speaker" in low:
            rank = 0
        elif "speaker" in low:
            rank = 1
        elif "headphone" in low or "headset" in low:
            rank = 2
        else:
            rank = 3
        ranked.append((rank, index))
    return [index for _, index in sorted(ranked)]


def _input_candidates(hint: str) -> list[int]:
    ranked: list[tuple[int, int]] = []
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) < 1:
            continue
        low = str(device["name"]).lower()
        if hint:
            if hint not in low:
                continue
            ranked.append((0, index))
            continue
        if any(skip in low for skip in ("steam", "stereo mix", "hands-free", "headset", "line in")):
            continue
        if "camera" in low:
            rank = 0
        elif "microphone" in low or "mic" in low:
            rank = 1
        else:
            rank = 2
        ranked.append((rank, index))
    return [index for _, index in sorted(ranked)]


class AudioEngine:
    def __init__(self, sounds: list[Sound], speaker_key: str = "", microphone_key: str = "") -> None:
        self.sounds = {sound.id: sound for sound in sounds}
        self.catalog = sounds
        self.output_key = speaker_key.strip()
        self.input_key = microphone_key.strip()
        self._probed_outputs: list[dict] | None = None
        self._probed_inputs: list[dict] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stream = None
        self.in_stream = None
        self.output_name: str | None = None
        self.input_name: str | None = None
        self.input_rate = 16000
        self.output_rate = RATE
        self.output_error: str | None = None
        self.channels = 1
        self.fx_gain = 0.85
        self.talk_gain = 1.0
        self.oneshot = np.zeros(0, dtype=np.float32)
        self.talk = np.zeros(0, dtype=np.float32)
        self.loop_clip: np.ndarray | None = None
        self.loop_id: str | None = None
        self.loop_pos = 0
        self.listeners: dict[asyncio.Queue, str] = {}
        self.talking: dict[str, float] = {}
        self.hold_input = False
        self.loudness = 0.0
        self._pre = np.zeros(0, dtype=np.float32)
        self._grab: list[np.ndarray] | None = None
        self._lock = __import__("threading").Lock()
        self._cb_logged = False

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def output_choices(self) -> list[dict]:
        if self._probed_outputs is not None:
            return self._probed_outputs
        return list_audio_devices("output")

    def input_choices(self) -> list[dict]:
        if self._probed_inputs is not None:
            return self._probed_inputs
        return list_audio_devices("input")

    def refresh_devices(self) -> None:
        self._probed_outputs = self._working_devices("output")
        self._probed_inputs = self._working_devices("input")
        if self._probed_inputs and not any(item["key"] == self.input_key for item in self._probed_inputs):
            self.input_key = self._probed_inputs[0]["key"]
        print("Speakers: " + ", ".join(item["label"] for item in self._probed_outputs), flush=True)
        print("Microphones: " + ", ".join(item["label"] for item in self._probed_inputs), flush=True)

    def _working_devices(self, kind: str) -> list[dict]:
        want_output = kind == "output"
        ranked: list[tuple] = []
        for index, device in enumerate(sd.query_devices()):
            channels = int(device["max_output_channels"] if want_output else device["max_input_channels"])
            if channels < 1:
                continue
            raw = str(device["name"])
            if _skipped(raw):
                continue
            host = _host_name(int(device["hostapi"]))
            ranked.append((_audio_sort(raw, kind), _host_rank(host), index, raw))
        ranked.sort()
        found: list[dict] = []
        seen: set[str] = set()
        active = self.output_key if want_output else ""
        for _sort, _rank, index, raw in ranked:
            label = friendly_name(raw)
            marker = label.lower()
            if marker in seen:
                continue
            key = device_key(index)
            if key == active or _try_device(kind, index):
                seen.add(marker)
                found.append({"key": key, "label": label})
        return found

    def ensure_output(self) -> None:
        if sd is None:
            raise AudioUnavailable("Speaker playback is unavailable because sounddevice is not installed.")
        if self.stream is not None and self.stream.active:
            return
        errors: list[str] = []
        chosen = resolve_device(self.output_key, "output") if self.output_key else None
        if chosen is not None:
            error = self._open_output(chosen, sd.query_devices(chosen))
            if error is None:
                return
            errors.append(error)
        elif self.output_key:
            errors.append("The saved speaker is not connected.")
        for choice in self.output_choices():
            index = resolve_device(choice["key"], "output")
            if index is None or index == chosen:
                continue
            error = self._open_output(index, sd.query_devices(index))
            if error is None:
                return
            errors.append(error)
        self.output_error = "; ".join(errors) if errors else "No speakers were found."
        raise AudioUnavailable("Could not open the speakers. Check that a playback device is connected in Windows.")

    def use_output(self, key: str) -> str:
        index = resolve_device(key, "output")
        if index is None:
            raise AudioUnavailable("That speaker is not connected.")
        self.output_key = key
        self._close_output()
        error = self._open_output(index, sd.query_devices(index))
        if error:
            self.output_error = error
            raise AudioUnavailable("Could not play through that speaker. Pick another one.")
        self._blip()
        return self.output_name or friendly_name(key)

    def use_input(self, key: str) -> str:
        index = resolve_device(key, "input")
        if index is None:
            raise AudioUnavailable("That microphone is not connected.")
        listening = bool(self.listeners)
        self.input_key = key
        self._stop_input()
        try:
            self.ensure_input()
        except AudioUnavailable:
            raise
        name = self.input_name or friendly_name(key)
        if not listening:
            self._stop_input()
        return name

    def _open_output(self, index: int, info: dict) -> str | None:
        channels = 2 if int(info["max_output_channels"]) >= 2 else 1
        rates = [RATE]
        native = int(info.get("default_samplerate") or 0)
        if native and native not in rates:
            rates.append(native)
        last = "could not open"
        for rate in rates:
            stream = None
            try:
                self.output_rate = int(rate)
                stream = sd.OutputStream(
                    device=index,
                    samplerate=rate,
                    channels=channels,
                    dtype="float32",
                    callback=self._callback,
                    blocksize=1024,
                )
                stream.start()
                self.stream = stream
                self.channels = channels
                self.output_name = friendly_name(str(info["name"]))
                self.output_key = device_key(index)
                self.output_error = None
                return None
            except Exception as exc:
                last = str(exc)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
        return f"{info['name']}: {last}"

    def ensure_input(self) -> int:
        if sd is None:
            raise AudioUnavailable("Room audio is unavailable because sounddevice is not installed.")
        if self.in_stream is not None and self.in_stream.active:
            return self.input_rate
        indexes: list[int] = []
        chosen = resolve_device(self.input_key, "input") if self.input_key else None
        if chosen is not None:
            indexes.append(chosen)
        if chosen is None:
            for choice in self.input_choices():
                index = resolve_device(choice["key"], "input")
                if index is not None:
                    indexes.append(index)
        errors: list[str] = []
        for index in indexes:
            info = sd.query_devices(index)
            native = int(info.get("default_samplerate") or 0)
            rates = [rate for rate in (native, 48000, 44100, 16000) if rate]
            seen: set[int] = set()
            opened = False
            for rate in rates:
                if rate in seen:
                    continue
                seen.add(rate)
                stream = None
                try:
                    stream = sd.InputStream(
                        device=index,
                        samplerate=rate,
                        channels=1,
                        dtype="float32",
                        callback=self._on_input,
                    )
                    stream.start()
                    self.in_stream = stream
                    self.input_rate = int(rate)
                    self.input_name = friendly_name(str(info["name"]))
                    self.input_key = device_key(index)
                    opened = True
                    return self.input_rate
                except Exception as exc:
                    errors.append(f"{friendly_name(str(info['name']))}: {exc}")
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
            if self.input_key and chosen is not None and not opened:
                break
        detail = errors[0] if errors else "No microphone was found."
        raise AudioUnavailable(f"Could not open the room microphone. {detail}")

    def add_listener(self, queue: asyncio.Queue, client_id: str) -> None:
        self.listeners[queue] = client_id[:64]

    def remove_listener(self, queue: asyncio.Queue) -> None:
        self.listeners.pop(queue, None)
        if not self.listeners and not self.hold_input:
            self._stop_input()

    def any_talking(self) -> bool:
        now = time.monotonic()
        return any(until > now for until in self.talking.values())

    def release_input_hold(self) -> None:
        self.hold_input = False
        if not self.listeners:
            self._stop_input()

    def start_grab(self) -> tuple[np.ndarray, int]:
        with self._lock:
            pre = self._pre.copy()
            self._grab = []
        return pre, int(self.input_rate or RATE)

    def stop_grab(self) -> tuple[np.ndarray, int]:
        with self._lock:
            parts = self._grab or []
            self._grab = None
            rate = int(self.input_rate or RATE)
        if not parts:
            return np.zeros(0, dtype=np.float32), rate
        return np.concatenate(parts), rate

    def mark_talking(self, client_id: str) -> None:
        self.talking[client_id[:64]] = time.monotonic() + 0.5

    def clear_talking(self, client_id: str) -> None:
        self.talking.pop(client_id[:64], None)

    def is_talking(self, client_id: str) -> bool:
        return self.talking.get(client_id[:64], 0) > time.monotonic()

    def set_talk_gain(self, gain: float) -> None:
        with self._lock:
            self.talk_gain = gain

    def add_talk(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return
        with self._lock:
            self.talk = np.concatenate([self.talk, samples])
            limit = RATE // 2
            if self.talk.size > limit:
                self.talk = self.talk[-limit:]

    def play(self, sound_id: str, volume: float, loop: bool) -> bool | None:
        sound = self.sounds.get(sound_id)
        if sound is None:
            return None
        self.ensure_output()
        with self._lock:
            self.fx_gain = volume
            if loop and sound.loop:
                if self.loop_id == sound.id:
                    self.loop_clip = None
                    self.loop_id = None
                    self.loop_pos = 0
                    return False
                self.loop_clip = sound.samples
                self.loop_id = sound.id
                self.loop_pos = 0
                return True
            self.oneshot = np.concatenate([self.oneshot, sound.samples])
            limit = RATE * 20
            if self.oneshot.size > limit:
                self.oneshot = self.oneshot[-limit:]
            return True

    def stop(self) -> None:
        with self._lock:
            self.oneshot = np.zeros(0, dtype=np.float32)
            self.talk = np.zeros(0, dtype=np.float32)
            self.loop_clip = None
            self.loop_id = None
            self.loop_pos = 0

    def status(self) -> dict:
        with self._lock:
            looping = self.loop_id
        return {
            "speaker": {
                "ok": self.stream is not None and bool(getattr(self.stream, "active", False)),
                "name": self.output_name,
                "key": self.output_key,
                "error": self.output_error,
            },
            "microphone": {
                "ok": self.in_stream is not None and bool(getattr(self.in_stream, "active", False)),
                "name": self.input_name,
                "key": self.input_key,
            },
            "looping": looping,
            "listeners": len(self.listeners),
        }

    def close(self) -> None:
        self._stop_input()
        self._close_output()

    def _close_output(self) -> None:
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _blip(self) -> None:
        tone = _fade(_tone(880, 0.16, decay=8, amp=0.35))
        with self._lock:
            self.oneshot = np.concatenate([self.oneshot, tone])

    def _stop_input(self) -> None:
        if self.hold_input:
            return
        stream = self.in_stream
        self.in_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _on_input(self, indata, frames, time_info, status) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        mono = np.asarray(indata[:, 0] if getattr(indata, "ndim", 1) > 1 else indata, dtype=np.float32).reshape(-1)
        if mono.size:
            self.loudness = float(np.sqrt(np.mean(mono * mono)))
            with self._lock:
                self._pre = np.concatenate([self._pre, mono])
                keep = max(int(self.input_rate or RATE), 8000)
                if self._pre.size > keep:
                    self._pre = self._pre[-keep:]
                if self._grab is not None:
                    self._grab.append(mono.copy())
        pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(self._fanout, pcm)

    def _fanout(self, pcm: bytes) -> None:
        for queue in list(self.listeners):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    def _render(self, frames: int) -> np.ndarray:
        out = np.zeros(frames, dtype=np.float32)
        if self.oneshot.size:
            count = min(frames, self.oneshot.size)
            out[:count] += self.oneshot[:count] * self.fx_gain
            self.oneshot = self.oneshot[count:]
        clip = self.loop_clip
        if clip is not None and clip.size:
            pos = self.loop_pos
            filled = 0
            while filled < frames:
                take = min(frames - filled, int(clip.size - pos))
                if take <= 0:
                    break
                out[filled : filled + take] += clip[pos : pos + take] * self.fx_gain
                filled += take
                pos += take
                if pos >= clip.size:
                    pos = 0
            self.loop_pos = pos
        if self.talk.size:
            count = min(frames, self.talk.size)
            out[:count] += self.talk[:count] * self.talk_gain
            self.talk = self.talk[count:]
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def _callback(self, outdata, frames, time_info, status) -> None:
        try:
            with self._lock:
                if self.output_rate == RATE:
                    mix = self._render(frames)
                else:
                    needed = max(1, int(round(frames * RATE / self.output_rate)))
                    mix = resample(self._render(needed), RATE, self.output_rate)
                    if mix.size < frames:
                        mix = np.pad(mix, (0, frames - mix.size))
                    mix = mix[:frames]
            if outdata.ndim == 1:
                outdata[:] = mix
            else:
                for channel in range(outdata.shape[1]):
                    outdata[:, channel] = mix
        except Exception as exc:
            outdata.fill(0)
            if not self._cb_logged:
                self._cb_logged = True
                print(f"Speaker callback error: {exc}", flush=True)
