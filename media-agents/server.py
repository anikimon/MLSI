import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agents as ml

BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8200"))
DB_PATH = Path(os.environ.get("MEDIA_DB", str(BASE_DIR / "data" / "media.db"))).resolve()
MAX_BODY = 2_000_000
BATCH_LIMIT = int(os.environ.get("ANALYZE_BATCH_LIMIT", "10"))
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL DEFAULT 'en',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    feed_id INTEGER REFERENCES feeds(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    published_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    translated_title TEXT NOT NULL DEFAULT '',
    translated_text TEXT NOT NULL DEFAULT '',
    tonality TEXT NOT NULL DEFAULT 'neutral',
    tonality_score REAL NOT NULL DEFAULT 0,
    tonality_reason TEXT NOT NULL DEFAULT '',
    aggression_level REAL NOT NULL DEFAULT 0,
    aggression_reason TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT 'не определён',
    target_type TEXT NOT NULL DEFAULT 'unknown',
    target_reason TEXT NOT NULL DEFAULT '',
    engines TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    stats TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
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
    if db.execute("SELECT COUNT(*) AS c FROM feeds").fetchone()["c"] == 0:
        for feed in ml.DEFAULT_FEEDS:
            db.execute(
                "INSERT OR IGNORE INTO feeds (name, url, language, created_at) VALUES (?, ?, ?, ?)",
                (feed["name"], feed["url"], feed["language"], now_iso()),
            )
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
        raise ApiError(413, "Слишком большой объём данных")
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


def article_dict(row, analysis=None):
    data = {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "summary": row["summary"],
        "text": row["text"],
        "link": row["link"],
        "language": row["language"],
        "published_at": row["published_at"],
        "created_at": row["created_at"],
        "analyzed": analysis is not None,
    }
    if analysis is not None:
        data.update(
            {
                "translated_title": analysis["translated_title"],
                "translated_text": analysis["translated_text"],
                "tonality": analysis["tonality"],
                "tonality_score": analysis["tonality_score"],
                "tonality_reason": analysis["tonality_reason"],
                "aggression_level": analysis["aggression_level"],
                "aggression_reason": analysis["aggression_reason"],
                "target": analysis["target"],
                "target_type": analysis["target_type"],
                "target_reason": analysis["target_reason"],
                "engines": json.loads(analysis["engines"]),
                "model": analysis["model"],
                "analyzed_at": analysis["created_at"],
            }
        )
    return data


def collect_feed(db, feed):
    try:
        items = ml.fetch_feed(feed["url"])
    except Exception as exc:  # noqa: BLE001
        return {"feed": feed["name"], "added": 0, "error": str(exc)}
    added = 0
    for item in items:
        fingerprint = ml.fingerprint(feed["name"], item["title"], item["link"])
        exists = db.execute("SELECT 1 FROM articles WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if exists:
            continue
        summary = item["summary"]
        text = summary
        db.execute(
            """INSERT INTO articles
               (feed_id, source, title, summary, text, link, language, published_at, fingerprint, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                feed["id"],
                feed["name"],
                item["title"],
                summary,
                text,
                item["link"],
                feed["language"],
                ml.parse_date(item["published"]),
                fingerprint,
                now_iso(),
            ),
        )
        added += 1
    return {"feed": feed["name"], "added": added, "error": None}


def fetch_articles(db, only, limit):
    query = """SELECT a.* FROM articles a
               LEFT JOIN analyses an ON an.article_id = a.id
               WHERE 1 = 1"""
    if only == "analyzed":
        query += " AND an.article_id IS NOT NULL"
    elif only == "unanalyzed":
        query += " AND an.article_id IS NULL"
    query += " ORDER BY a.published_at DESC, a.id DESC LIMIT ?"
    rows = db.execute(query, (limit,)).fetchall()
    result = []
    for row in rows:
        analysis = db.execute("SELECT * FROM analyses WHERE article_id = ?", (row["id"],)).fetchone()
        result.append(article_dict(row, analysis))
    return result


def run_analysis(db, row, key):
    article = {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "text": row["text"] or row["summary"] or row["title"],
        "link": row["link"],
    }
    network = ml.Network(key)
    result = network.analyze(article)
    db.execute(
        """INSERT INTO analyses
           (article_id, translated_title, translated_text, tonality, tonality_score, tonality_reason,
            aggression_level, aggression_reason, target, target_type, target_reason, engines, model, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(article_id) DO UPDATE SET
             translated_title = excluded.translated_title,
             translated_text = excluded.translated_text,
             tonality = excluded.tonality,
             tonality_score = excluded.tonality_score,
             tonality_reason = excluded.tonality_reason,
             aggression_level = excluded.aggression_level,
             aggression_reason = excluded.aggression_reason,
             target = excluded.target,
             target_type = excluded.target_type,
             target_reason = excluded.target_reason,
             engines = excluded.engines,
             model = excluded.model,
             created_at = excluded.created_at""",
        (
            row["id"],
            result["translated_title"],
            result["translated_text"],
            result["tonality"],
            result["tonality_score"],
            result["tonality_reason"],
            result["aggression_level"],
            result["aggression_reason"],
            result["target"],
            result["target_type"],
            result["target_reason"],
            json.dumps(result["engines"], ensure_ascii=False),
            result["model"],
            now_iso(),
        ),
    )
    db.commit()
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaAgents/1.0"

    def log_message(self, fmt, *args):
        print(f"[media-agents] {self.address_string()} {fmt % args}", file=sys.stderr)

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
        except Exception as exc:  # noqa: BLE001
            print(f"[media-agents] Ошибка: {exc!r}", file=sys.stderr)
            self.send_json(500, {"error": "Внутренняя ошибка сервера"})

    def api(self, db, method, path):
        key = ml.default_key()

        if path == "/api/health" and method == "GET":
            return 200, {"ok": True, "ai_enabled": bool(key)}

        if path == "/api/feeds" and method == "GET":
            rows = db.execute("SELECT * FROM feeds ORDER BY name").fetchall()
            return 200, {"feeds": [dict(row) for row in rows]}

        if path == "/api/feeds" and method == "POST":
            data = read_json(self)
            name = clean_text(data.get("name"), 120, required=True, field="название")
            url = clean_text(data.get("url"), 500, required=True, field="URL")
            if not url.startswith(("http://", "https://")):
                raise ApiError(400, "URL должен начинаться с http:// или https://")
            language = clean_text(data.get("language", "en"), 10) or "en"
            try:
                cursor = db.execute(
                    "INSERT INTO feeds (name, url, language, created_at) VALUES (?, ?, ?, ?)",
                    (name, url, language, now_iso()),
                )
            except sqlite3.IntegrityError:
                raise ApiError(409, "Такой RSS-источник уже добавлен")
            db.commit()
            return 201, {"id": cursor.lastrowid}

        match = re.fullmatch(r"/api/feeds/(\d+)", path)
        if match and method == "DELETE":
            db.execute("DELETE FROM feeds WHERE id = ?", (int(match.group(1)),))
            db.commit()
            return 200, {"ok": True}

        if path == "/api/collect" and method == "POST":
            data = read_json(self)
            feed_id = data.get("feed_id")
            if feed_id:
                feeds = db.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchall()
            else:
                feeds = db.execute("SELECT * FROM feeds ORDER BY name").fetchall()
            reports = [collect_feed(db, feed) for feed in feeds]
            db.commit()
            return 200, {
                "results": reports,
                "added": sum(item["added"] for item in reports),
                "errors": [item for item in reports if item["error"]],
            }

        if path == "/api/articles" and method == "GET":
            params = {}
            if "?" in self.path:
                for pair in self.path.split("?", 1)[1].split("&"):
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        params[name] = value
            only = params.get("only", "all")
            if only not in ("all", "analyzed", "unanalyzed"):
                only = "all"
            try:
                limit = min(int(params.get("limit", "100")), 500)
            except ValueError:
                limit = 100
            return 200, {"articles": fetch_articles(db, only, limit)}

        if path == "/api/articles" and method == "POST":
            data = read_json(self)
            title = clean_text(data.get("title"), 500, required=True, field="заголовок")
            text = clean_text(data.get("text", ""), 20000, field="текст")
            source = clean_text(data.get("source", "Ручная вставка"), 120) or "Ручная вставка"
            link = clean_text(data.get("link", ""), 1000, field="ссылка")
            language = clean_text(data.get("language", "ru"), 10) or "ru"
            fingerprint = ml.fingerprint(source, title, link or now_iso())
            cursor = db.execute(
                """INSERT INTO articles
                   (feed_id, source, title, summary, text, link, language, published_at, fingerprint, created_at)
                   VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, title, text, text, link, language, now_iso(), fingerprint, now_iso()),
            )
            db.commit()
            return 201, {"id": cursor.lastrowid}

        match = re.fullmatch(r"/api/articles/(\d+)", path)
        if match:
            article_id = int(match.group(1))
            if method == "GET":
                row = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
                if not row:
                    raise ApiError(404, "Материал не найден")
                analysis = db.execute("SELECT * FROM analyses WHERE article_id = ?", (article_id,)).fetchone()
                return 200, {"article": article_dict(row, analysis)}
            if method == "DELETE":
                db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
                db.commit()
                return 200, {"ok": True}

        match = re.fullmatch(r"/api/articles/(\d+)/analyze", path)
        if match and method == "POST":
            article_id = int(match.group(1))
            row = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            if not row:
                raise ApiError(404, "Материал не найден")
            with _lock:
                result = run_analysis(db, row, key)
            return 200, {"analysis": result}

        if path == "/api/analyze" and method == "POST":
            data = read_json(self)
            try:
                limit = min(int(data.get("limit", BATCH_LIMIT)), BATCH_LIMIT)
            except (TypeError, ValueError):
                limit = BATCH_LIMIT
            rows = db.execute(
                """SELECT a.* FROM articles a
                   LEFT JOIN analyses an ON an.article_id = a.id
                   WHERE an.article_id IS NULL
                   ORDER BY a.published_at DESC, a.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            processed = []
            for row in rows:
                with _lock:
                    run_analysis(db, row, key)
                processed.append(row["id"])
            return 200, {"processed": len(processed), "ids": processed, "ai_enabled": bool(key)}

        if path == "/api/stats" and method == "GET":
            rows = db.execute(
                """SELECT a.source, a.title, an.tonality, an.tonality_score, an.aggression_level, an.target
                   FROM analyses an JOIN articles a ON a.id = an.article_id"""
            ).fetchall()
            records = [
                {
                    "source": row["source"],
                    "title": row["title"],
                    "tonality": row["tonality"],
                    "tonality_score": row["tonality_score"],
                    "aggression_level": row["aggression_level"],
                    "target": row["target"],
                }
                for row in rows
            ]
            stats = ml.build_stats(records)
            stats["total_articles"] = db.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
            stats["analyzed_articles"] = len(records)
            return 200, {"stats": stats}

        if path == "/api/reports" and method == "GET":
            rows = db.execute("SELECT id, title, model, created_at FROM reports ORDER BY id DESC LIMIT 50").fetchall()
            return 200, {"reports": [dict(row) for row in rows]}

        if path == "/api/reports" and method == "POST":
            rows = db.execute(
                """SELECT a.source, a.title, an.tonality, an.tonality_score, an.aggression_level, an.target
                   FROM analyses an JOIN articles a ON a.id = an.article_id"""
            ).fetchall()
            articles = [
                {
                    "source": row["source"],
                    "title": row["title"],
                    "tonality": row["tonality"],
                    "tonality_score": row["tonality_score"],
                    "aggression_level": row["aggression_level"],
                    "target": row["target"],
                }
                for row in rows
            ]
            stats = ml.build_stats(articles)
            summary = ml.SummaryAgent(key).summarize(stats, articles)
            title = f"Отчёт от {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')}"
            model = ml.DEEPSEEK_MODEL if key else "local"
            cursor = db.execute(
                "INSERT INTO reports (title, content, stats, model, created_at) VALUES (?, ?, ?, ?, ?)",
                (title, summary, json.dumps(stats, ensure_ascii=False), model, now_iso()),
            )
            db.commit()
            return 201, {
                "report": {"id": cursor.lastrowid, "title": title, "content": summary, "stats": stats, "model": model}
            }

        match = re.fullmatch(r"/api/reports/(\d+)", path)
        if match and method == "GET":
            row = db.execute("SELECT * FROM reports WHERE id = ?", (int(match.group(1)),)).fetchone()
            if not row:
                raise ApiError(404, "Отчёт не найден")
            return 200, {
                "report": {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["content"],
                    "stats": json.loads(row["stats"]),
                    "model": row["model"],
                    "created_at": row["created_at"],
                }
            }

        raise ApiError(404, "Не найдено")


def create_server(host=HOST, port=PORT, path=None):
    global DB_PATH
    if path:
        DB_PATH = Path(path).resolve()
        init_db(DB_PATH).close()
    return ThreadingHTTPServer((host, port), Handler)


def main():
    init_db(DB_PATH).close()
    print(f"[media-agents] База данных: {DB_PATH}")
    print(f"[media-agents] ИИ: {'DeepSeek подключён' if ml.default_key() else 'ключ не найден — локальный режим'}")
    print(f"[media-agents] Панель: http://{HOST}:{PORT}")
    server = create_server(HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[media-agents] Остановка")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
