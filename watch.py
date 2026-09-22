"""Armed watch: motion, loud sound, alert, and 10-second clips."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import wave
from pathlib import Path

import numpy as np

from audio import RATE, AudioEngine, AudioUnavailable
from camera import Camera

CLIP_SECONDS = 10
PRE_SECONDS = 1


def motion_threshold(sensitivity: int) -> float:
    span = (max(1, min(100, sensitivity)) - 1) / 99
    return 0.06 - span * (0.06 - 0.008)


def sound_threshold(sensitivity: int) -> float:
    span = (max(1, min(100, sensitivity)) - 1) / 99
    return 0.22 - span * (0.22 - 0.012)


class SentryWatch:
    def __init__(self, root: Path, camera: Camera, audio: AudioEngine, config: dict, save_config) -> None:
        self.root = root / "clips"
        self.camera = camera
        self.audio = audio
        self.config = config
        self.save_config = save_config
        self.recording = False
        self.cooldown_until = 0.0
        self.armed_at = 0.0
        self._motion_hits = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())

    def public(self) -> dict:
        events = self._read_log()
        return {
            "armed": bool(self.config.get("armed")),
            "recording": self.recording,
            "clipLimit": int(self.config.get("clip_limit") or 100),
            "motionSensitivity": int(self.config.get("motion_sensitivity") or 45),
            "soundSensitivity": int(self.config.get("sound_sensitivity") or 55),
            "cooldown": int(self.config.get("alert_cooldown") or 20),
            "motion": round(min(100.0, self.camera.motion / 0.08 * 100.0), 1),
            "loud": round(min(100.0, self.audio.loudness / 0.2 * 100.0), 1),
            "count": len(events),
            "events": events[:40],
        }

    async def apply(self, changes: dict) -> dict:
        config = self.config
        if "armed" in changes:
            config["armed"] = bool(changes["armed"])
        if "clipLimit" in changes:
            config["clip_limit"] = _clamp(changes["clipLimit"], 100, 1, 500)
        if "motion" in changes:
            config["motion_sensitivity"] = _clamp(changes["motion"], 45, 1, 100)
        if "sound" in changes:
            config["sound_sensitivity"] = _clamp(changes["sound"], 55, 1, 100)
        if "cooldown" in changes:
            config["alert_cooldown"] = _clamp(changes["cooldown"], 20, 5, 180)
        await asyncio.to_thread(self.save_config, config)
        if config["armed"]:
            self.armed_at = time.monotonic()
            self._motion_hits = 0
            await self._ensure_sensors()
        else:
            self.audio.release_input_hold()
            self.armed_at = 0.0
        await asyncio.to_thread(self._prune)
        return self.public()

    async def clear(self) -> dict:
        await asyncio.to_thread(self._wipe)
        return self.public()

    def clip_dir(self, clip_id: str) -> Path | None:
        if not _safe_id(clip_id):
            return None
        folder = self.root / clip_id
        if not folder.is_dir():
            return None
        return folder

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if not self.config.get("armed"):
                self._motion_hits = 0
                continue
            try:
                await self._ensure_sensors()
            except Exception:
                continue
            if self.armed_at and time.monotonic() - self.armed_at < 3:
                continue
            if self.recording or time.time() < self.cooldown_until:
                continue
            motion = self.camera.motion
            loud = 0.0 if self.audio.any_talking() else self.audio.loudness
            motion_hit = motion >= motion_threshold(int(self.config.get("motion_sensitivity") or 45))
            self._motion_hits = self._motion_hits + 1 if motion_hit else 0
            sound_hit = loud >= sound_threshold(int(self.config.get("sound_sensitivity") or 55))
            reasons = []
            if self._motion_hits >= 2:
                reasons.append("motion")
            if sound_hit:
                reasons.append("sound")
            if not reasons:
                continue
            self.recording = True
            asyncio.create_task(self._incident("+".join(reasons), motion, loud))

    async def _ensure_sensors(self) -> None:
        camera = self.camera
        if not camera.is_running():
            index = self.config.get("camera_index")
            if index is None:
                devices = camera.devices()
                index = devices[0]["index"] if devices else 0
                self.config["camera_index"] = int(index)
                await asyncio.to_thread(self.save_config, self.config)
            await asyncio.to_thread(camera.arm, int(index))
        self.audio.hold_input = True
        try:
            await asyncio.to_thread(self.audio.ensure_input)
        except AudioUnavailable:
            return

    async def _incident(self, reason: str, motion: float, loud: float) -> None:
        clip_id = time.strftime("%Y%m%d-%H%M%S")
        print(f"Armed alert: {reason} {clip_id}", flush=True)
        try:
            try:
                await asyncio.to_thread(self.audio.play, "alert", 0.9, False)
            except AudioUnavailable:
                pass
            pre_frames = self.camera.recent_jpegs()
            pre_audio, rate = self.audio.start_grab()
            frames = list(pre_frames)
            fps = max(2, int(self.config.get("fps") or 8))
            interval = 1 / fps
            try:
                deadline = asyncio.get_running_loop().time() + (CLIP_SECONDS - PRE_SECONDS)
                while asyncio.get_running_loop().time() < deadline and len(frames) < fps * CLIP_SECONDS:
                    jpeg = self.camera.latest_jpeg()
                    if jpeg:
                        frames.append(jpeg)
                    await asyncio.sleep(interval)
            finally:
                after_audio, rate = self.audio.stop_grab()
            audio = np.concatenate([pre_audio, after_audio]) if after_audio.size else pre_audio
            frames = frames[: fps * CLIP_SECONDS]
            await asyncio.to_thread(self._store, clip_id, reason, motion, loud, frames, audio, rate, fps)
        finally:
            self.recording = False
            self.cooldown_until = time.time() + int(self.config.get("alert_cooldown") or 20)
            self._motion_hits = 0

    def _store(self, clip_id: str, reason: str, motion: float, loud: float, frames: list[bytes], audio: np.ndarray, rate: int, fps: int) -> None:
        folder = self.root / clip_id
        folder.mkdir(parents=True, exist_ok=True)
        saved = 0
        for index, jpeg in enumerate(frames):
            if not jpeg:
                continue
            (folder / f"{index:03d}.jpg").write_bytes(jpeg)
            saved += 1
        if audio.size:
            _write_wav(folder / "audio.wav", audio, rate or RATE)
        meta = {
            "id": clip_id,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "motion": round(motion, 4),
            "loud": round(float(loud), 4),
            "frames": saved,
            "fps": fps,
            "audio": (folder / "audio.wav").exists(),
        }
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        log = self._read_log()
        log.insert(0, meta)
        self._write_log(log)
        self._prune()

    def _prune(self) -> None:
        limit = int(self.config.get("clip_limit") or 100)
        log = [item for item in self._read_log() if (self.root / str(item.get("id", ""))).is_dir()]
        extra = log[limit:]
        log = log[:limit]
        self._write_log(log)
        for item in extra:
            folder = self.root / str(item.get("id", ""))
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        known = {str(item.get("id")) for item in log}
        if self.root.exists():
            for folder in self.root.iterdir():
                if folder.is_dir() and folder.name not in known:
                    shutil.rmtree(folder, ignore_errors=True)

    def _wipe(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_log([])

    def _read_log(self) -> list[dict]:
        path = self.root / "log.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _write_log(self, events: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "log.json"
        path.write_text(json.dumps(events), encoding="utf-8")


def _safe_id(clip_id: str) -> bool:
    return len(clip_id) == 15 and clip_id[8] == "-" and clip_id.replace("-", "").isdigit()


def _clamp(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    ints = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(max(8000, int(rate)))
        handle.writeframes(ints.tobytes())
