"""Sentry Room status window. Starts the monitor without a command window."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_ID = "SentryRoom.Desktop"


def _log(message: str) -> None:
    try:
        with (ROOT / "desktop.log").open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        return


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        return


def _write_icon(png_path: Path, ico_path: Path) -> None:
    data = png_path.read_bytes()
    header = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    entry = bytes((0, 0, 0, 0)) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
    entry += len(data).to_bytes(4, "little") + (22).to_bytes(4, "little")
    ico_path.write_bytes(header + entry + data)


def _phone_address(links: list[dict]) -> str:
    trusted = [link.get("https") for link in links if link.get("trusted") and link.get("https")]
    if trusted:
        return str(trusted[0])
    remote = [link.get("https") for link in links if link.get("kind") != "local" and link.get("https")]
    if remote:
        return str(remote[0])
    return ""


def _page_latency(port: int) -> int | None:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=2) as response:
            response.read()
    except Exception:
        return None
    return int((time.perf_counter() - started) * 1000)


def install_login_start() -> Path:
    """Open Sentry Room after Windows sign-in."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        raise OSError("APPDATA is missing.")
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    for name in ("Sentry Room.vbs", "Sentry Room.lnk", "Sentry Room.cmd"):
        try:
            (startup / name).unlink()
        except OSError:
            pass
    launcher = startup / "Sentry Room.cmd"
    folder = str(ROOT)
    launcher.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'cd /d "{folder}"',
                "ping -n 21 127.0.0.1 >nul",
                f'if exist "{folder}\\.venv\\Scripts\\pythonw.exe" (',
                f'  start "" "{folder}\\.venv\\Scripts\\pythonw.exe" "{folder}\\status_ui.py"',
                ") else (",
                f'  call "{folder}\\Launch Sentry.bat"',
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return launcher


def create_shortcuts(pythonw: Path, ico: Path) -> None:
    """Put a Start Menu and Desktop shortcut the user can pin to the taskbar."""
    if sys.platform != "win32":
        return
    programs = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path.home() / "Desktop"
    targets = [programs / "Sentry Room.lnk", desktop / "Sentry Room.lnk"]
    script = f"""
$paths = @({", ".join(repr(str(path)) for path in targets)})
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $paths) {{
  $link = $shell.CreateShortcut($path)
  $link.TargetPath = '{pythonw}'
  $link.Arguments = '"{ROOT / "status_ui.py"}"'
  $link.WorkingDirectory = '{ROOT}'
  $link.IconLocation = '{ico},0'
  $link.Description = 'Sentry Room status'
  $link.WindowStyle = 1
  $link.Save()
}}
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=False,
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        _log(f"shortcut skipped: {exc}")


def main() -> None:
    if "--gui" not in sys.argv:
        sys.argv.append("--gui")

    _set_app_id()
    _log("opening status window")

    import tkinter as tk
    from tkinter import messagebox

    from app import STATE, desktop_status, replace_code, serve
    from camera import write_icon

    icon_png = ROOT / "web" / "icon.png"
    icon_ico = ROOT / "sentry.ico"
    try:
        write_icon(icon_png)
        _write_icon(icon_png, icon_ico)
    except Exception as exc:
        _log(f"icon skipped: {exc}")

    pythonw = Path(sys.executable)
    if pythonw.name.lower() == "python.exe":
        sibling = pythonw.with_name("pythonw.exe")
        if sibling.exists():
            pythonw = sibling
    create_shortcuts(pythonw, icon_ico)

    def server() -> None:
        try:
            asyncio.run(serve())
        except SystemExit:
            if not STATE.get("ready"):
                STATE["error"] = "The page port is already in use. Close the other Sentry Room window."
        except Exception:
            STATE["error"] = "Sentry Room stopped."
            _log(traceback.format_exc())
        finally:
            STATE["stopped"] = True

    threading.Thread(target=server, name="sentry-server", daemon=False).start()

    root = tk.Tk()
    root.title("Sentry Room")
    root.configure(bg="#0c1218")
    root.geometry("460x640")
    root.minsize(420, 560)
    try:
        root.iconbitmap(str(icon_ico))
    except Exception:
        pass

    card = "#16202b"
    text = "#f4f8fc"
    muted = "#d5e2ef"
    line = "#8eabc6"

    frame = tk.Frame(root, bg=card, highlightbackground=line, highlightthickness=1)
    frame.pack(fill="both", expand=True, padx=16, pady=16)

    tk.Label(frame, text="SENTRY ROOM", bg=card, fg="#9ec8ff", font=("Segoe UI", 11, "bold")).pack(
        anchor="w", padx=16, pady=(16, 0)
    )
    tk.Label(frame, text="Status", bg=card, fg=text, font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=16)

    rows: dict[str, tk.Label] = {}

    def add_row(key: str, caption: str) -> None:
        block = tk.Frame(frame, bg=card)
        block.pack(fill="x", padx=16, pady=4)
        tk.Label(block, text=caption, bg=card, fg=muted, font=("Segoe UI", 9), width=12, anchor="w").pack(side="left")
        value = tk.Label(block, text="…", bg=card, fg=text, font=("Segoe UI", 11), anchor="w", justify="left", wraplength=280)
        value.pack(side="left", fill="x", expand=True)
        rows[key] = value

    for key, caption in (
        ("run", "Running"),
        ("armed", "Armed"),
        ("viewers", "Watching"),
        ("camera", "Camera"),
        ("picture", "Picture"),
        ("latency", "Latency"),
        ("speaker", "Speaker"),
        ("mic", "Microphone"),
        ("clips", "Clips"),
        ("phone", "Phone"),
    ):
        add_row(key, caption)

    note = tk.Label(frame, text="", bg=card, fg="#ffd0cb", font=("Segoe UI", 10), wraplength=390, justify="left")
    note.pack(anchor="w", padx=16, pady=(8, 0))

    tk.Label(
        frame,
        text="A Sentry Room shortcut is on the Desktop and Start menu. Right-click it and choose Pin to taskbar.",
        bg=card,
        fg=muted,
        font=("Segoe UI", 9),
        wraplength=390,
        justify="left",
    ).pack(anchor="w", padx=16, pady=(4, 0))

    def open_page() -> None:
        status = desktop_status()
        port = status["port"] if status else 8787
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def reset_code() -> None:
        dialog = tk.Toplevel(root)
        dialog.title("New sign-in code")
        dialog.configure(bg=card)
        dialog.transient(root)
        dialog.grab_set()
        dialog.resizable(False, False)
        tk.Label(dialog, text="Choose a new code", bg=card, fg=text, font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        tk.Label(
            dialog,
            text="The current code is not required. Phones will need to sign in again. The code is not shown here after you save it.",
            bg=card,
            fg=muted,
            wraplength=360,
            justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=16, pady=(0, 10))

        def field(label: str) -> tk.Entry:
            tk.Label(dialog, text=label, bg=card, fg=muted, font=("Segoe UI", 10)).pack(anchor="w", padx=16)
            entry = tk.Entry(
                dialog,
                show="*",
                bg="#0e1620",
                fg=text,
                insertbackground=text,
                relief="flat",
                highlightthickness=1,
                highlightbackground="#8eabc6",
                highlightcolor="#9ec8ff",
                font=("Segoe UI", 14),
            )
            entry.pack(fill="x", padx=16, pady=(2, 10), ipady=6)
            return entry

        first = field("New code")
        second = field("Confirm code")
        error = tk.Label(dialog, text="", bg=card, fg="#ffffff", font=("Segoe UI", 10), wraplength=360, justify="left")
        error.pack(anchor="w", padx=16)

        def reveal() -> None:
            shown = first.cget("show") == ""
            first.configure(show="*" if shown else "")
            second.configure(show="*" if shown else "")

        def save() -> None:
            problem = replace_code(first.get(), second.get())
            first.delete(0, "end")
            second.delete(0, "end")
            if problem:
                error.configure(text=problem, bg="#8a3030")
                return
            dialog.destroy()
            note.configure(text="Code changed. Sign in again on each phone.", fg="#9dffc0", bg=card)

        buttons = tk.Frame(dialog, bg=card)
        buttons.pack(fill="x", padx=16, pady=16)
        tk.Button(
            buttons, text="Show", command=reveal, bg="#1c2836", fg=text, relief="flat", font=("Segoe UI", 10), padx=10, pady=6
        ).pack(side="left")
        tk.Button(
            buttons,
            text="Cancel",
            command=dialog.destroy,
            bg="#1c2836",
            fg=text,
            relief="flat",
            font=("Segoe UI", 10),
            padx=10,
            pady=6,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            buttons,
            text="Save code",
            command=save,
            bg="#1565c0",
            fg="#ffffff",
            activebackground="#1d74d6",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6,
        ).pack(side="right")
        first.focus_set()

    actions = tk.Frame(frame, bg=card)
    actions.pack(fill="x", padx=16, pady=16)
    tk.Button(
        actions,
        text="Open monitor",
        command=open_page,
        bg="#1565c0",
        fg="#ffffff",
        activebackground="#1d74d6",
        activeforeground="#ffffff",
        relief="flat",
        font=("Segoe UI", 11, "bold"),
        padx=12,
        pady=8,
    ).pack(side="left")
    tk.Button(
        actions,
        text="New code",
        command=reset_code,
        bg="#1c2836",
        fg=text,
        activebackground="#243447",
        activeforeground=text,
        relief="flat",
        font=("Segoe UI", 11),
        padx=12,
        pady=8,
    ).pack(side="left", padx=8)

    def shutdown() -> None:
        if not messagebox.askokcancel("Sentry Room", "Close Sentry Room? The camera and page will turn off."):
            return
        stop = STATE.get("stop")
        loop = STATE.get("loop")
        if loop is not None and stop is not None:
            loop.call_soon_threadsafe(stop.set)
        root.destroy()

    tk.Button(
        frame, text="Quit", command=shutdown, bg="#1c2836", fg=text, relief="flat", font=("Segoe UI", 10), padx=12, pady=6
    ).pack(anchor="e", padx=16, pady=(0, 16))
    root.protocol("WM_DELETE_WINDOW", shutdown)

    def refresh() -> None:
        if STATE.get("error"):
            rows["run"].configure(text="Not running", fg="#ffd0cb")
            note.configure(text=STATE["error"], fg="#ffffff", bg="#8a3030")
        elif not STATE.get("ready"):
            rows["run"].configure(text="Starting…", fg=text)
        else:
            status = desktop_status()
            if status:
                latency = _page_latency(status["port"])
                rows["run"].configure(text="On", fg="#9dffc0")
                rows["armed"].configure(text="Recording" if status["recording"] else "Armed" if status["armed"] else "Off")
                watching = status["viewers"]
                rows["viewers"].configure(text=f"{watching} on the page" if watching else "Nobody")
                if status["cameraOn"] and not status["cameraError"]:
                    rows["camera"].configure(text="On")
                elif status["cameraError"]:
                    rows["camera"].configure(text=status["cameraError"])
                else:
                    rows["camera"].configure(text="Off")
                if status["pictureMs"] is None:
                    rows["picture"].configure(text="No picture yet")
                else:
                    rows["picture"].configure(text=f"{status['pictureMs']} ms delay")
                rows["latency"].configure(text="No reply" if latency is None else f"{latency} ms")
                rows["speaker"].configure(text=status["speaker"] or "Off")
                rows["mic"].configure(text=status["microphone"] or "Off")
                rows["clips"].configure(text=str(status["clips"]))
                rows["phone"].configure(text=_phone_address(status["links"]) or f"http://127.0.0.1:{status['port']}/")
        if not STATE.get("stopped"):
            root.after(1000, refresh)

    root.after(400, refresh)
    root.mainloop()
    stop = STATE.get("stop")
    loop = STATE.get("loop")
    if loop is not None and stop is not None and not stop.is_set():
        loop.call_soon_threadsafe(stop.set)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log(traceback.format_exc())
        raise
