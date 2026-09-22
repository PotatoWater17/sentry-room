"""Webcam capture for the room monitor."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
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
    img[:] = (252, 248, 244)
    cv2.circle(img, (90, 90), 64, (192, 101, 21), -1)
    cv2.circle(img, (90, 90), 26, (252, 248, 244), -1)
    cv2.circle(img, (104, 72), 8, (255, 255, 255), -1)
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
    def __init__(self, width: int, fps: int, quality: int, mirror: bool) -> None:
        self.index: int | None = None
        self.width = int(width)
        self.fps = max(2, int(fps))
        self.quality = int(quality)
        self.mirror = bool(mirror)
        self.stop_flag = False
        self.restart_flag = False
        self._devices: list[dict] = []
        self._listed = False
        self.error = "Choose a camera to turn the picture on."
        self.frame_at = 0.0
        self.frame_w = 0
        self.frame_h = 0
        self._jpeg = self._message_frame(self.error)
        self.motion = 0.0
        self._prev_gray = None
        self._recent: deque[bytes] = deque(maxlen=max(2, self.fps))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def devices(self) -> list[dict]:
        if not self._listed:
            self._listed = True
            names = _pnp_camera_names()
            count = min(6, max(4, len(names)))
            labels = [names[index] if index < len(names) else f"Camera {index + 1}" for index in range(count)]
            self._remember_devices([
                {"index": index, "label": label, "width": 0, "height": 0}
                for index, label in enumerate(labels)
            ])
        with self._lock:
            return [dict(item) for item in self._devices]

    def arm(self, index: int) -> None:
        self.index = int(index)
        self.stop_flag = False
        self.restart_flag = True
        self.error = "Starting camera..."
        if self.is_running():
            return
        self._thread = threading.Thread(target=self._run, name="sentry-camera", daemon=True)
        self._thread.start()

    def release(self) -> None:
        self.stop_flag = True
        self.restart_flag = False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self.index = None
            self.frame_at = 0.0
            self.motion = 0.0
            self._prev_gray = None
            self._recent.clear()
            self.error = "Camera is off."
            self._jpeg = self._message_frame("Camera is off. Choose one to turn the picture on.")

    def latest_jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def recent_jpegs(self) -> list[bytes]:
        with self._lock:
            return list(self._recent)

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

    def stop(self) -> None:
        self.release()

    def _set_frame(self, jpeg: bytes, error: str | None, width: int, height: int, live: bool) -> None:
        with self._lock:
            self._jpeg = jpeg
            self.error = error
            self.frame_w = width
            self.frame_h = height
            if live:
                self.frame_at = time.monotonic()
                self._recent.append(jpeg)

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

    def _measure_motion(self, frame: np.ndarray) -> None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        previous = self._prev_gray
        self._prev_gray = small
        if previous is None or previous.shape != small.shape:
            self.motion = 0.0
            return
        delta = cv2.absdiff(previous, small)
        self.motion = float(np.mean(delta)) / 255.0

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
        opened_index: int | None = None
        try:
            while not self.stop_flag:
                started = time.monotonic()
                wanted = self.index
                if wanted is None:
                    if cap is not None:
                        cap.release()
                        cap = None
                        opened_index = None
                    time.sleep(0.2)
                    continue
                if cap is None or self.restart_flag or opened_index != wanted:
                    self.restart_flag = False
                    if cap is not None:
                        cap.release()
                        cap = None
                    cap = self._open(wanted)
                    opened_index = wanted
                    misses = 0
                    if cap is None:
                        message = (
                            f"Could not open camera {wanted + 1}. "
                            "Choose another one, and allow desktop apps to use the camera in Windows."
                        )
                        self._set_frame(self._message_frame(message), message, 960, 540, False)
                        time.sleep(1.2)
                        continue
                    print(f"Camera {wanted + 1} is on.", flush=True)
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
                        opened_index = None
                        time.sleep(1.2)
                        continue
                    time.sleep(0.05)
                    continue
                misses = 0
                self._measure_motion(frame)
                jpeg = self._encode(frame)
                if jpeg:
                    self._set_frame(jpeg, None, frame.shape[1], frame.shape[0], True)
                delay = period - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(delay)
        finally:
            if cap is not None:
                cap.release()
            if self.index is not None:
                print("Camera is off.", flush=True)
