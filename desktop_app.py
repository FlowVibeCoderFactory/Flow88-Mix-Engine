from __future__ import annotations

import socket
import threading
import time

import uvicorn

from server import app
from runtime_config import get_runtime_settings


HOST = "127.0.0.1"
PORT = get_runtime_settings().port
WINDOW_URL = f"http://{HOST}:{PORT}"


def run_server() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


def wait_for_server(timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)


def main() -> None:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop mode requires pywebview. Install desktop extras with: pip install -r requirements/desktop.txt"
        ) from exc

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    wait_for_server()

    webview.create_window(
        "Flow88 Mix Engine",
        WINDOW_URL,
        width=1100,
        height=800,
        background_color="#1E1E1E",
    )
    webview.start()


if __name__ == "__main__":
    main()
