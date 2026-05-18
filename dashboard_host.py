"""
NEXUS / babyrat — desktop dashboard host.

Starts the FastAPI relay (same as `server.py`) and lets you open the UI either
in the system browser or in an embedded window (pywebview → WebView2 on Windows).

Usage:
  python dashboard_host.py
  python dashboard_host.py --host 0.0.0.0 --port 8080 --mode embedded

Optional:
  pip install pywebview   # required for embedded mode
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

# Project root (so server finds dashboard_working.html, network_config.json, etc.)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)


def _load_network_defaults():
    try:
        import json

        p = os.path.join(_ROOT, "network_config.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            return (
                str(cfg.get("server_bind_host") or os.environ.get("HOST", "127.0.0.1")),
                int(cfg.get("server_port") or os.environ.get("PORT", 8080)),
            )
    except Exception:
        pass
    return os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080"))


def _local_dashboard_url(port: int) -> str:
    """URL for browser / webview on this machine (works when server binds 0.0.0.0)."""
    return f"http://127.0.0.1:{port}/"


def _wait_http_ready(url: str, timeout: float = 45.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _run_uvicorn(host: str, port: int) -> None:
    import uvicorn

    from server import app

    uvicorn.run(
        app,
        host=host,
        port=int(port),
        backlog=2048,
        ws_ping_interval=20,
        ws_ping_timeout=90,
        timeout_keep_alive=120,
    )


def _start_server_background(host: str, port: int) -> threading.Thread:
    th = threading.Thread(target=_run_uvicorn, args=(host, port), daemon=True)
    th.start()
    return th


def run_embedded(url: str, title: str = "NEXUS · Remote Ops") -> None:
    try:
        import webview
    except ImportError as e:
        raise RuntimeError(
            "Embedded mode needs pywebview. Install with:  pip install pywebview\n"
            f"Original error: {e}"
        ) from e
    webview.create_window(title, url, width=1280, height=800)
    webview.start()


def _gui_main(host: str, port: int, mode) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    host_d, port_d = _load_network_defaults()
    if host == "":
        host = host_d
    if port <= 0:
        port = port_d

    root = tk.Tk()
    root.title("NEXUS · Dashboard host")
    root.minsize(420, 260)

    frm = ttk.Frame(root, padding=12)
    frm.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    ttk.Label(frm, text="Bind address (0.0.0.0 = all interfaces)").grid(row=0, column=0, sticky="w")
    host_var = tk.StringVar(value=host)
    ttk.Entry(frm, textvariable=host_var, width=28).grid(row=1, column=0, sticky="ew", pady=(0, 8))

    ttk.Label(frm, text="Port").grid(row=2, column=0, sticky="w")
    port_var = tk.StringVar(value=str(port))
    ttk.Entry(frm, textvariable=port_var, width=10).grid(row=3, column=0, sticky="w", pady=(0, 8))

    ttk.Label(frm, text="Dashboard UI").grid(row=4, column=0, sticky="w")
    mode_var = tk.StringVar(value=mode or "browser")
    bf = ttk.Frame(frm)
    bf.grid(row=5, column=0, sticky="w", pady=(0, 6))
    ttk.Radiobutton(bf, text="System browser (Chrome / Edge / …)", variable=mode_var, value="browser").pack(anchor="w")
    ttk.Radiobutton(
        bf,
        text="Embedded window (pywebview — same features as browser)",
        variable=mode_var,
        value="embedded",
    ).pack(anchor="w")

    hint = (
        "Uses the same FastAPI + WebSocket stack as `python server.py`.\n"
        "Embedded mode often behaves more smoothly behind strict firewalls (no extra browser profile)."
    )
    ttk.Label(frm, text=hint, wraplength=400, justify="left").grid(row=6, column=0, sticky="w", pady=(4, 8))

    status = ttk.Label(frm, text="Idle.", foreground="#555")
    status.grid(row=7, column=0, sticky="w")

    def set_status(msg: str) -> None:
        status.config(text=msg)

    def on_start() -> None:
        h = host_var.get().strip() or "127.0.0.1"
        try:
            p = int(str(port_var.get()).strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.")
            return
        if p < 1 or p > 65535:
            messagebox.showerror("Invalid port", "Port must be between 1 and 65535.")
            return

        m = mode_var.get()
        set_status("Starting server…")
        root.update_idletasks()

        _start_server_background(h, p)
        url = _local_dashboard_url(p)
        if not _wait_http_ready(url):
            set_status("Server failed to start (timeout).")
            messagebox.showerror(
                "Server not responding",
                f"Nothing answered at {url}\nCheck that port {p} is free and bind address is valid.",
            )
            return

        set_status(f"Server OK — {url}")

        if m == "embedded":
            root.withdraw()
            try:
                run_embedded(url)
            except RuntimeError as ex:
                root.deiconify()
                messagebox.showerror("Embedded mode", str(ex))
                set_status("Embedded mode failed.")
                return
            os._exit(0)

        webbrowser.open(url)
        for w in frm.winfo_children():
            w.destroy()
        ttk.Label(frm, text="Server is running", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(frm, text=url, wraplength=420, foreground="#0066aa").pack(anchor="w", pady=(0, 10))
        ttk.Label(
            frm,
            text="Keep this window open while you use the dashboard in your browser.\n"
            "Closing it stops the relay server.",
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(frm, text="Open dashboard again in browser", command=lambda: webbrowser.open(url)).pack(anchor="w", pady=(0, 6))
        ttk.Button(frm, text="Quit (stop server)", command=on_close).pack(anchor="w")
        set_status("Browser opened — server active.")

    ttk.Button(frm, text="Start server & open dashboard", command=on_start).grid(row=8, column=0, sticky="ew", pady=(8, 0))

    ttk.Label(
        frm,
        text="Tip: CLI —  python dashboard_host.py --mode embedded --port 8080",
        font=("TkDefaultFont", 8),
    ).grid(row=9, column=0, sticky="w", pady=(10, 0))

    frm.columnconfigure(0, weight=1)

    def on_close() -> None:
        root.destroy()
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="NEXUS dashboard host (browser or embedded Web UI).")
    parser.add_argument("--host", default="", help="Bind host (default: from network_config.json or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Port (default: from network_config.json or 8080)")
    parser.add_argument(
        "--mode",
        choices=("browser", "embedded", "server-only"),
        default="",
        help="Skip GUI: browser | embedded | server-only (no UI, same as plain server)",
    )
    parser.add_argument("--no-gui", action="store_true", help="Same as --mode server-only")
    args = parser.parse_args(argv)

    host_d, port_d = _load_network_defaults()
    host = args.host or host_d
    port = args.port or port_d

    if args.no_gui or args.mode == "server-only":
        _run_uvicorn(host, port)
        return 0

    if args.mode in ("browser", "embedded"):
        _start_server_background(host, port)
        url = _local_dashboard_url(port)
        if not _wait_http_ready(url):
            print(f"ERROR: server did not become ready at {url}", file=sys.stderr)
            return 1
        if args.mode == "browser":
            webbrowser.open(url)
            print(f"Server running at {url} — press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return 0
        run_embedded(url)
        return 0

    return _gui_main(host, port, None)


if __name__ == "__main__":
    raise SystemExit(main())
