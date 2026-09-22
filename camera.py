"""Webcam capture for the room monitor."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:
    pass

_BACKENDS = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY)


def write_icon(path: Path) -> None:
    if path.exists():
        return
    img = np.zeros((180, 180, 3), np.uint8)
    img[:] = (18, 24, 16)
    cv2.circle(img, (90, 90), 64, (2, 162, 240), -1)
    cv2.circle(img, (90, 90), 26, (16, 22, 14), -1)
    cv2.circle(img, (104, 72), 8, (245, 248, 255), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if len(trial) > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _pnp_camera_names() -> list[str]:
    script = (
        "Get-PnpDevice -Class Camera,Image -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Status -eq 'OK' -and $_.FriendlyName } | "
        "ForEach-Object { $_.FriendlyName }"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in output.splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


class Camera:
    def __init__(self, index: int, width: int, fps: int, quality: int, mirror: bool) -> None:
        self.index = int(index)
        self.width = int(width)
        self.fps = max(2, int(fps))
        self.quality = int(quality)
        self.mirror = bool(mirror)
        self.stop_flag = False
        self.restart_flag = False
        self._did_scan = False
        self._devices: list[dict] = []
        self.error = "Starting camera..."
        self.frame_at = 0.0
        self.frame_w = 0
        self.frame_h = 0
        self._jpeg = self._message_frame(self.error)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="sentry-camera", daemon=True)
        self._thread.start()

    def latest_jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def snapshot(self) -> dict:
        with self._lock:
            age = None if self.frame_at <= 0 else time.monotonic() - self.frame_at
            return {
                "error": self.error,
                "index": self.index,
                "age": age,
                "width": self.frame_w,
                "height": self.frame_h,
                "devices": [dict(item) for item in self._devices],
            }

    def _remember_devices(self, devices: list[dict]) -> None:
        with self._lock:
            self._devices = devices

    def set_index(self, index: int) -> None:
        self.index = int(index)
        self.restart_flag = True

    def stop(self) -> None:
        self.stop_flag = True
        self._thread.join(timeout=2)

    def _set_frame(self, jpeg: bytes, error: str | None, width: int, height: int, live: bool) -> None:
        with self._lock:
            self._jpeg = jpeg
            self.error = error
            self.frame_w = width
            self.frame_h = height
            if live:
                self.frame_at = time.monotonic()

    def _message_frame(self, message: str) -> bytes:
        img = np.zeros((540, 960, 3), np.uint8)
        img[:] = (22, 28, 20)
        cv2.putText(img, "Sentry Room", (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (2, 162, 240), 2, cv2.LINE_AA)
        y = 250
        for line in _wrap(message, 52)[:6]:
            cv2.putText(img, line, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 236, 232), 1, cv2.LINE_AA)
            y += 36
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return b""
        return buf.tobytes()

    def _open(self, index: int) -> cv2.VideoCapture | None:
        height = max(240, int(self.width * 9 / 16))
        for backend in _BACKENDS:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
        return None

    def _encode(self, frame: np.ndarray) -> bytes | None:
        h, w = frame.shape[:2]
        if w > self.width:
            scale = self.width / w
            frame = cv2.resize(frame, (self.width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        if self.mirror:
            frame = cv2.flip(frame, 1)
        text = time.strftime("%Y-%m-%d  %H:%M:%S")
        bar_top = max(0, frame.shape[0] - 42)
        cv2.rectangle(frame, (0, bar_top), (frame.shape[1], frame.shape[0]), (0, 0, 0), -1)
        cv2.putText(frame, text, (12, frame.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (245, 245, 245), 1, cv2.LINE_AA)
        cv2.circle(frame, (frame.shape[1] - 28, 28), 8, (50, 50, 230), -1)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            return None
        return buf.tobytes()

    def _run(self) -> None:
        cap: cv2.VideoCapture | None = None
        misses = 0
        period = 1 / self.fps
        try:
            while not self.stop_flag:
                started = time.monotonic()
                if cap is None or self.restart_flag:
                    self.restart_flag = False
                    if cap is not None:
                        cap.release()
                        cap = None
                    cap = self._open(self.index)
                    misses = 0
                    if cap is None:
                        if not self._did_scan:
                            self._did_scan = True
                            found = self._probe()
                            self._remember_devices(found)
                            if found and not any(item["index"] == self.index for item in found):
                                self.index = int(found[0]["index"])
                                continue
                        message = (
                            f"No camera found at index {self.index}. "
                            "Choose another camera below, and allow desktop apps to use the camera in Windows."
                        )
                        self._set_frame(self._message_frame(message), message, 960, 540, False)
                        time.sleep(1.2)
                        continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    misses += 1
                    if misses >= 8:
                        message = (
                            "The camera stopped responding. Close other apps that are using it, "
                            "and allow desktop apps to use the camera in Windows privacy settings."
                        )
                        self._set_frame(self._message_frame(message), message, 960, 540, False)
                        cap.release()
                        cap = None
                        time.sleep(1.2)
                        continue
                    time.sleep(0.05)
                    continue
                misses = 0
                jpeg = self._encode(frame)
                if jpeg:
                    self._set_frame(jpeg, None, frame.shape[1], frame.shape[0], True)
                if not self._did_scan:
                    self._did_scan = True
                    cap.release()
                    cap = None
                    self._set_frame(self._message_frame("Checking connected cameras..."), "Checking connected cameras...", 960, 540, False)
                    found = self._probe()
                    if not any(item["index"] == self.index for item in found):
                        found.insert(0, {
                            "index": self.index,
                            "label": f"Camera {self.index + 1}",
                            "width": self.frame_w,
                            "height": self.frame_h,
                        })
                    self._remember_devices(found)
                    continue
                delay = period - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(delay)
        finally:
            if cap is not None:
                cap.release()

    def _probe(self) -> list[dict]:
        names = _pnp_camera_names()
        found: list[dict] = []
        for index in range(6):
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                continue
            ok, frame = cap.read()
            width = height = 0
            if ok and frame is not None:
                height, width = frame.shape[:2]
            cap.release()
            label = names[len(found)] if len(found) < len(names) else f"Camera {index + 1}"
            if width and height:
                label = f"{label} ({width}x{height})"
            found.append({"index": index, "label": label, "width": width, "height": height})
        if not found:
            found.append({"index": self.index, "label": f"Camera {self.index + 1}", "width": 0, "height": 0})
        print("Cameras: " + ", ".join(item["label"] for item in found), flush=True)
        return found
