"""
Локальный запуск для проверки на компьютере (не нужен для работы на телефоне).

    python dev_server.py

Открывает http://localhost:8000 — приложение из папки docs/,
а POST http://localhost:8000/api вызывает ту же функцию, что и в Yandex Cloud.
Ключи берутся из файла .env (он не попадает в git).
"""

import http.server
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
sys.path.insert(0, os.path.join(ROOT, "function"))
import index  # noqa: E402  (функция из папки function/)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(ROOT, "docs"), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # чтобы правки сразу были видны
        super().end_headers()

    def _send(self, result):
        self.send_response(result["statusCode"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(result["body"].encode("utf-8"))

    def do_OPTIONS(self):
        self._send(index.handler({"httpMethod": "OPTIONS"}))

    def do_POST(self):
        if self.path != "/api":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
        self._send(index.handler({"httpMethod": "POST", "body": body, "isBase64Encoded": False}))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"http://localhost:{port}  (адрес сервера в настройках: http://localhost:{port}/api)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
