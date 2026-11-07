"""Small launcher window: pick an order image, press Start, watch the run.

    python -m i2c gui          (or double-click Launch.bat)

The flow itself runs as a subprocess (`python -m i2c run <image>`), exactly as on the command
line, so the GUI adds no behaviour of its own - it only starts, streams and reports the run.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
STATUS = {0: ("Done - order and invoice created", "#1f6b3a"),
          3: ("Stopped for manual review - see log", "#a36a00"),
          4: ("Error - see log", "#a3312a"),
          2: ("Usage / config error - see log", "#a3312a")}


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Fakturama Image-to-Cash")
        self.geometry("860x640")
        self.minsize(640, 480)
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.image = tk.StringVar(value=str(ROOT / "samples" / "order_01.png"))
        self.minimize = tk.BooleanVar(value=True)
        self._thumb = None
        self._build()
        self._preview()

    # ---- layout ------------------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Order image:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.image).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse...", command=self._browse).grid(row=0, column=2)
        top.columnconfigure(1, weight=1)

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="x")
        self.thumb = ttk.Label(mid)
        self.thumb.pack(side="left")
        btns = ttk.Frame(mid, padding=(14, 0))
        btns.pack(side="left", fill="y")
        self.start_btn = ttk.Button(btns, text="Start", command=self._start)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)
        ttk.Button(btns, text="Open last run folder", command=self._open_last).pack(fill="x", pady=2)
        ttk.Checkbutton(btns, text="Minimize this window while running\n(keeps Fakturama uncovered)",
                        variable=self.minimize).pack(anchor="w", pady=(8, 0))
        self.status = tk.Label(btns, text="Make sure Fakturama is open, then press Start.",
                               anchor="w", justify="left", wraplength=420, font=("Segoe UI", 11, "bold"))
        self.status.pack(anchor="w", pady=(12, 0))

        log = ttk.Frame(self, padding=10)
        log.pack(fill="both", expand=True)
        self.log = tk.Text(log, wrap="none", font=("Consolas", 9), state="disabled")
        sb = ttk.Scrollbar(log, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _browse(self) -> None:
        p = filedialog.askopenfilename(title="Choose an order image", initialdir=ROOT / "samples",
                                       filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")])
        if p:
            self.image.set(p)
            self._preview()

    def _preview(self) -> None:
        try:
            from PIL import Image, ImageTk
            im = Image.open(self.image.get())
            im.thumbnail((150, 200))
            self._thumb = ImageTk.PhotoImage(im)
            self.thumb.configure(image=self._thumb, text="")
        except Exception:  # noqa: BLE001 - preview is cosmetic
            self.thumb.configure(image="", text="(no preview)")

    # ---- run ---------------------------------------------------------------------------
    def _start(self) -> None:
        image = Path(self.image.get())
        if not image.is_file():
            self._set_status(f"Image not found: {image}", "#a3312a")
            return
        self._clear_log()
        self._set_status("Running...", "#2f6fb0")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        cmd = [sys.executable, "-m", "i2c", "run", str(image)]
        self._append(f"> {' '.join(cmd)}\n")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        if self.minimize.get():
            self.after(300, self.iconify)
        self.after(100, self._drain)

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _drain(self) -> None:
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self._finished()
                    return
                self._append(line)
        except queue.Empty:
            self.after(100, self._drain)

    def _finished(self) -> None:
        code = self.proc.wait() if self.proc else -1
        self.proc = None
        text, color = STATUS.get(code, (f"Stopped (exit {code})", "#a3312a"))
        self._set_status(text, color)
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.deiconify()
        self.lift()

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._append("\n[stopped by user - Fakturama is left as it is]\n")

    def _close(self) -> None:
        self._stop()
        self.destroy()

    # ---- helpers -----------------------------------------------------------------------
    def _open_last(self) -> None:
        runs = sorted((p for p in ARTIFACTS.glob("*") if p.is_dir() and p.name != "probe"),
                      key=lambda p: p.stat().st_mtime)
        if runs:
            os.startfile(runs[-1])  # noqa: S606 - Windows-only tool

    def _set_status(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main() -> int:
    Launcher().mainloop()
    return 0
