import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FASTEMBED_CACHE_PATH", "/srv/mlsi/models/fastembed")
os.environ.setdefault("HF_HOME", "/srv/mlsi/models")

from fastembed import TextEmbedding

MODEL_NAME = os.environ.get("EMB_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MODEL = None
LOCK = threading.Lock()


def load_model():
    global MODEL
    if MODEL is None:
        MODEL = TextEmbedding(MODEL_NAME)
    return MODEL


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "model": MODEL_NAME})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/embed":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            texts = data.get("texts") or []
            if not isinstance(texts, list) or not texts or not all(isinstance(item, str) for item in texts):
                raise ValueError("texts must be a non-empty list of strings")
            with LOCK:
                vectors = [list(map(float, vector)) for vector in load_model().embed(texts)]
            self._send(200, {"vectors": vectors})
        except Exception as exc:
            self._send(500, {"error": str(exc)[:200]})


if __name__ == "__main__":
    host = os.environ.get("EMB_HOST", "127.0.0.1")
    port = int(os.environ.get("EMB_PORT", "8091"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"embeddings service on {host}:{port} model={MODEL_NAME}", flush=True)
    try:
        with LOCK:
            load_model()
        print("model ready", flush=True)
    except Exception as exc:
        print("model warmup failed:", repr(exc)[:200], flush=True)
    server.serve_forever()
