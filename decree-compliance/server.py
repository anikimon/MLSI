import base64
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agents
import extract
import rubric

BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8300"))
DB_PATH = Path(os.environ.get("COMPLIANCE_DB", str(BASE_DIR / "data" / "compliance.db"))).resolve()
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))).resolve()
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
MAX_BODY = MAX_UPLOAD_MB * 1024 * 1024 * 4 // 3 + 1_000_000
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    filename TEXT NOT NULL DEFAULT '',
    stored_path TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    material_id INTEGER PRIMARY KEY REFERENCES materials(id) ON DELETE CASCADE,
    results TEXT NOT NULL,
    index_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    visual TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    engines TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(path=None):
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(target), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db(path=None):
    db = connect(path)
    db.executescript(SCHEMA)
    db.commit()
    return db


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def read_json(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY:
        raise ApiError(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
    try:
        data = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, "Некорректный JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "Ожидается объект JSON")
    return data


def clean_text(value, max_len, required=False, field="значение"):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(400, f"Поле «{field}» должно быть строкой")
    text = value.strip()
    if required and not text:
        raise ApiError(400, f"Поле «{field}» обязательно")
    if len(text) > max_len:
        raise ApiError(400, f"Поле «{field}» длиннее {max_len} символов")
    return text


class Handler(BaseHTTPRequestHandler):
    server_version = "DecreeCompliance/1.0"

    def log_message(self, fmt, *args):
        print(f"[compliance] {self.address_string()} {fmt % args}", file=sys.stderr)

    def db(self):
        return connect(DB_PATH)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path):
        target = (BASE_DIR / path.lstrip("/")).resolve()
        if BASE_DIR not in target.parents and target != BASE_DIR:
            raise ApiError(404, "Файл не найден")
        if not target.is_file():
            raise ApiError(404, "Файл не найден")
        content = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        if (mime or "").startswith("text") or (mime or "") == "application/javascript":
            mime = (mime or "text/plain") + "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def handle_request(self, method):
        try:
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "Метод не поддерживается")
                self.send_static("index.html" if path == "/" else path)
                return
            db = self.db()
            try:
                status, payload = self.api(db, method, path)
                self.send_json(status, payload)
            finally:
                db.close()
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})
        except extract.ExtractionError as error:
            self.send_json(400, {"error": str(error)})
        except Exception as exc:  # noqa: BLE001
            print(f"[compliance] Ошибка: {exc!r}", file=sys.stderr)
            self.send_json(500, {"error": "Внутренняя ошибка сервера"})

    def api(self, db, method, path):
        key = agents.default_key()

        if path == "/api/health" and method == "GET":
            return 200, {
                "ok": True,
                "ai_enabled": bool(key),
                "ffmpeg": bool(extract.which_ffmpeg()),
                "asr": extract.ASR_ENABLED,
                "vision": bool(agents.VISION_API_KEY and agents.VISION_API_BASE),
            }

        if path == "/api/rubric" and method == "GET":
            return 200, {
                "decree": rubric.DECREE,
                "sections": [
                    {"id": section["id"], "code": section["code"], "title": section["title"],
                     "instruction": section["instruction"],
                     "provisions": [
                         {"id": pid, "title": title, "description": description}
                         for pid, title, description, _ in section["provisions"]
                     ]}
                    for section in rubric.SECTIONS
                ],
                "statuses": rubric.STATUS_LABELS,
            }

        if path == "/api/materials" and method == "GET":
            rows = db.execute(
                """SELECT m.id, m.title, m.kind, m.filename, m.created_at,
                          (an.material_id IS NOT NULL) AS analyzed
                   FROM materials m LEFT JOIN analyses an ON an.material_id = m.id
                   ORDER BY m.id DESC LIMIT 200"""
            ).fetchall()
            return 200, {"materials": [dict(row) for row in rows]}

        if path == "/api/materials" and method == "POST":
            return self.create_material(db)

        match = re.fullmatch(r"/api/materials/(\d+)", path)
        if match:
            material_id = int(match.group(1))
            if method == "GET":
                row = db.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
                if not row:
                    raise ApiError(404, "Материал не найден")
                return 200, {"material": material_dict(row)}
            if method == "DELETE":
                row = db.execute("SELECT stored_path FROM materials WHERE id = ?", (material_id,)).fetchone()
                if row and row["stored_path"]:
                    try:
                        Path(row["stored_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
                db.commit()
                return 200, {"ok": True}

        match = re.fullmatch(r"/api/materials/(\d+)/analyze", path)
        if match and method == "POST":
            return self.analyze_material(db, int(match.group(1)), key)

        match = re.fullmatch(r"/api/materials/(\d+)/report", path)
        if match and method == "GET":
            row = db.execute("SELECT * FROM analyses WHERE material_id = ?", (int(match.group(1)),)).fetchone()
            if not row:
                raise ApiError(404, "Отчёт ещё не сформирован")
            return 200, {"report": report_dict(row)}

        raise ApiError(404, "Не найдено")

    def create_material(self, db):
        data = read_json(self)
        title = clean_text(data.get("title"), 300, field="название")
        text = clean_text(data.get("text", ""), 200000, field="текст")
        content_b64 = data.get("content_base64")
        filename = clean_text(data.get("filename", ""), 300, field="имя файла")
        kind = "text"
        stored_path = ""
        frames = []

        if content_b64:
            try:
                content = base64.b64decode(content_b64, validate=False)
            except (ValueError, base64.binascii.Error):
                raise ApiError(400, "Некорректное содержимое файла")
            if not title:
                title = filename or "Без названия"
            upload_dir = UPLOAD_DIR
            upload_dir.mkdir(parents=True, exist_ok=True)
            stored = upload_dir / f"{uuid.uuid4().hex}{Path(filename).suffix}"
            stored.write_bytes(content)
            stored_path = str(stored)
            extracted = extract.extract_full(filename, content)
            text = extracted["text"]
            kind = extracted["kind"]
            if not text.strip():
                raise ApiError(400, "Не удалось извлечь текст из файла")

        if not title:
            title = text.strip().splitlines()[0][:120] if text.strip() else "Без названия"
        if not text.strip():
            raise ApiError(400, "Пустой материал: передайте текст или файл")

        cursor = db.execute(
            """INSERT INTO materials (title, kind, filename, stored_path, text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, kind, filename, stored_path, text, now_iso()),
        )
        db.commit()
        return 201, {"id": cursor.lastrowid, "kind": kind, "length": len(text), "preview": text[:500]}

    def analyze_material(self, db, material_id, key):
        row = db.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if not row:
            raise ApiError(404, "Материал не найден")
        frames = []
        if row["kind"] == "video" and row["stored_path"] and Path(row["stored_path"]).exists():
            try:
                frames = extract.extract_frames(Path(row["stored_path"]))
            except extract.ExtractionError:
                frames = []
        with _lock:
            result = agents.ComplianceNetwork(key).analyze(row["text"], row["title"], frames)
        db.execute(
            """INSERT INTO analyses (material_id, results, index_json, summary, visual, model, engines, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(material_id) DO UPDATE SET
                 results=excluded.results, index_json=excluded.index_json, summary=excluded.summary,
                 visual=excluded.visual, model=excluded.model, engines=excluded.engines, created_at=excluded.created_at""",
            (
                material_id,
                json.dumps(result["results"], ensure_ascii=False),
                json.dumps(result["index"], ensure_ascii=False),
                result["summary"],
                json.dumps(result["visual"], ensure_ascii=False) if result["visual"] else "",
                result["model"],
                json.dumps(result["engines"], ensure_ascii=False),
                now_iso(),
            ),
        )
        db.commit()
        return 200, {"report": {
            "index": result["index"],
            "summary": result["summary"],
            "visual": result["visual"],
            "model": result["model"],
            "engines": result["engines"],
        }}


def material_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "text": row["text"][:20000],
        "length": len(row["text"]),
    }


def report_dict(row):
    return {
        "material_id": row["material_id"],
        "results": json.loads(row["results"]),
        "index": json.loads(row["index_json"]),
        "summary": row["summary"],
        "visual": json.loads(row["visual"]) if row["visual"] else None,
        "model": row["model"],
        "engines": json.loads(row["engines"]),
        "created_at": row["created_at"],
    }


def create_server(host=HOST, port=PORT, path=None):
    global DB_PATH
    if path:
        DB_PATH = Path(path).resolve()
        init_db(DB_PATH).close()
    return ThreadingHTTPServer((host, port), Handler)


def main():
    init_db(DB_PATH).close()
    print(f"[compliance] База данных: {DB_PATH}")
    print(f"[compliance] ИИ: {'DeepSeek подключён' if agents.default_key() else 'локальный режим'}")
    print(f"[compliance] ffmpeg: {'найден' if extract.which_ffmpeg() else 'не найден (аудио/видео недоступны)'}")
    print(f"[compliance] Панель: http://{HOST}:{PORT}")
    server = create_server(HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[compliance] Остановка")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
