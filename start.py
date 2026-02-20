#!/usr/bin/env python3
"""
CSForge launcher — starts the backend and opens the frontend.
Run from the csforge/ directory: python start.py
"""

import os
import sys
import time
import subprocess
import threading
import webbrowser
import http.server
import socketserver

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT, "backend")
FRONTEND_DIR = os.path.join(ROOT, "frontend")
BACKEND_PORT = 7847
FRONTEND_PORT = 7848


def check_deps():
    missing = []
    for pkg, import_name in [
        ("flask", "flask"),
        ("watchdog", "watchdog"),
        ("tree_sitter", "tree_sitter"),
        ("tree_sitter_languages", "tree_sitter_languages"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[X] Missing dependencies: {', '.join(missing)}")
        print(f"  Run: pip install {' '.join(missing)}")
        print("  (tree-sitter packages are optional but recommended for reliable parsing)")
        if any(p in missing for p in ("flask", "watchdog")):
            sys.exit(1)
        # tree-sitter missing is non-fatal — parser falls back to regex
        print("  Continuing with regex fallback parser...")
        print()


def start_backend():
    print(f"  Starting backend on http://localhost:{BACKEND_PORT} ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND_DIR, "app.py")],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Stream backend output with prefix
    def stream():
        for line in proc.stdout:
            print(f"  [backend] {line.decode().rstrip()}")

    t = threading.Thread(target=stream, daemon=True)
    t.start()
    return proc


def find_free_port(start_port, attempts=20):
    """Return the first free TCP port at or above start_port."""
    import socket
    for port in range(start_port, start_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port found in range {start_port}–{start_port + attempts - 1}")


def start_frontend(port_holder: list):
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

    os.chdir(FRONTEND_DIR)
    try:
        port = find_free_port(FRONTEND_PORT)
    except OSError as exc:
        port_holder.append(None)
        print(f"\n  [frontend] ERROR: {exc}")
        return

    socketserver.TCPServer.allow_reuse_address = True
    port_holder.append(port)
    with socketserver.TCPServer(("", port), QuietHandler) as httpd:
        httpd.serve_forever()


def wait_for_backend(timeout=10):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"http://localhost:{BACKEND_PORT}/api/entities",
                timeout=1
            )
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    print()
    print("  ██████╗███████╗███████╗ ██████╗ ██████╗  ██████╗ ███████╗")
    print(" ██╔════╝██╔════╝██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝")
    print(" ██║     ███████╗█████╗  ██║   ██║██████╔╝██║  ███╗█████╗  ")
    print(" ██║     ╚════██║██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  ")
    print(" ╚██████╗███████║██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗")
    print("  ╚═════╝╚══════╝╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝")
    print()
    print("  C# Entity Explorer · Two-Way Sync · Live Mock API · Infra Generator")
    print()

    check_deps()

    backend_proc = start_backend()

    # Start frontend file server in background thread
    fe_port_holder: list = []
    fe_thread = threading.Thread(target=start_frontend, args=(fe_port_holder,), daemon=True)
    fe_thread.start()

    print(f"  Waiting for backend", end="", flush=True)
    ready = wait_for_backend(timeout=12)
    print()

    if not ready:
        print("  ✕ Backend failed to start. Check for errors above.")
        backend_proc.terminate()
        sys.exit(1)

    # Give the frontend thread a moment to bind its port
    deadline = time.time() + 5
    while not fe_port_holder and time.time() < deadline:
        time.sleep(0.05)

    fe_port = fe_port_holder[0] if fe_port_holder else None
    if fe_port is None:
        print("  ✕ Frontend server failed to start. Check for errors above.")
        backend_proc.terminate()
        sys.exit(1)

    if fe_port != FRONTEND_PORT:
        print(f"  [frontend] Port {FRONTEND_PORT} was busy — using {fe_port} instead.")

    url = f"http://localhost:{fe_port}"
    print(f"  ✓ Backend  →  http://localhost:{BACKEND_PORT}")
    print(f"  ✓ Frontend →  {url}")
    print()
    print("  Opening browser...")
    print()
    print("  Press Ctrl+C to stop")
    print()

    time.sleep(0.5)
    webbrowser.open(url)

    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        backend_proc.terminate()
        print("  ✓ Stopped")


if __name__ == "__main__":
    main()
