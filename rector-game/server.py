"""Простой статический сервер для игры «Проректор» (PWA).

Запуск:
    python server.py
Затем откройте http://127.0.0.1:8400

Для доступа с телефона в одной сети:
    $env:HOST = "0.0.0.0"; python server.py
Установка на главный экран и офлайн-режим требуют HTTPS или localhost.
"""
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8400"))


class Handler(BaseHTTPRequestHandler):
    server_version = "RectorGame/1.0"

    def log_message(self, fmt, *args):
        print(f"[rector] {self.address_string()} {fmt % args}", file=sys.stderr)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        target = (BASE_DIR / path.lstrip("/")).resolve()
        if (BASE_DIR not in target.parents and target != BASE_DIR) or not target.is_file():
            self.send_error(404, "Файл не найден")
            return
        content = target.read_bytes()
        if target.suffix == ".webmanifest":
            mime = "application/manifest+json; charset=utf-8"
        else:
            mime, _ = mimetypes.guess_type(str(target))
            if (mime or "").startswith("text") or mime in ("application/javascript", "application/json"):
                mime = (mime or "text/plain") + "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[rector] Игра: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[rector] Остановка")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
