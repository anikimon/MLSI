import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8100"))
DB_PATH = Path(os.environ.get("LAB_DB", str(BASE_DIR / "data" / "lab.db"))).resolve()
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
ALLOW_DEV_CODES = os.environ.get("ALLOW_DEV_CODES", "0") == "1"
CODE_TTL_SECONDS = int(os.environ.get("CODE_TTL_SECONDS", "600"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(14 * 24 * 3600)))
MAX_BODY = 1_500_000
ROLES = ("admin", "researcher", "interviewer")
QUESTION_TYPES = ("open", "single", "multiple", "scale")

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "no-reply@localhost").strip()
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Социологическая лаборатория").strip()
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1") == "1"
SMTP_SSL = os.environ.get("SMTP_SSL", "0") == "1"
SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "20"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc) - timedelta(days=1)


def password_hash(password, salt=None):
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    iterations = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return "pbkdf2_sha256${}${}${}".format(iterations, salt_bytes.hex(), digest.hex())


def check_password(password, stored):
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(computed.hex(), digest)
    except (ValueError, AttributeError):
        return False


def connect(path=None):
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(target), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_challenges (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surveys (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    tasks TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    type TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '[]',
    scale_min REAL,
    scale_max REAL,
    scale_step REAL
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT 'null'
);
CREATE TABLE IF NOT EXISTS survey_members (
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (survey_id, user_id)
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_answers_response ON answers(response_id);
CREATE INDEX IF NOT EXISTS idx_responses_survey ON responses(survey_id);
"""


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
        raise ApiError(413, "Слишком большой объём данных")
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
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


def normalize_email(value):
    email = clean_text(value, 254, required=True, field="email").lower()
    if not EMAIL_RE.match(email):
        raise ApiError(400, "Некорректный адрес электронной почты")
    return email


def deepseek_key():
    path = BASE_DIR / "key.txt"
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def send_code_email(recipient, code):
    if not SMTP_HOST:
        print(f"[sociolab] SMTP не настроен. Код для {recipient}: {code}", file=sys.stderr)
        return False
    message = EmailMessage()
    message["Subject"] = "Код подтверждения входа"
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    message["To"] = recipient
    message.set_content(
        f"Здравствуйте!\n\nКод подтверждения входа: {code}\n"
        f"Код действует {CODE_TTL_SECONDS // 60} минут.\n\n"
        "Если вы не входили в систему, проигнорируйте это письмо и смените пароль."
    )
    message.add_alternative(
        "<div style=\"font-family:Arial,sans-serif\">"
        "<h2>Подтверждение входа</h2>"
        f"<p>Код подтверждения: <b style=\"font-size:22px;letter-spacing:3px\">{code}</b></p>"
        f"<p>Код действует {CODE_TTL_SECONDS // 60} минут.</p>"
        "<p style=\"color:#888\">Если вы не входили в систему, проигнорируйте письмо.</p>"
        "</div>",
        subtype="html",
    )
    context = ssl.create_default_context()
    if SMTP_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
    try:
        if not SMTP_SSL and SMTP_STARTTLS:
            server.starttls(context=context)
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            server.close()
    return True


def build_stats(db, survey_id):
    questions = db.execute(
        "SELECT * FROM questions WHERE survey_id = ? ORDER BY position, id", (survey_id,)
    ).fetchall()
    responses = db.execute(
        "SELECT id FROM responses WHERE survey_id = ?", (survey_id,)
    ).fetchall()
    total = len(responses)
    answers = db.execute(
        """SELECT a.question_id, a.value FROM answers a
           JOIN responses r ON r.id = a.response_id
           WHERE r.survey_id = ?""",
        (survey_id,),
    ).fetchall()
    grouped = {}
    for row in answers:
        grouped.setdefault(row["question_id"], []).append(json.loads(row["value"]))

    result = {"total_responses": total, "questions": []}
    for q in questions:
        values = grouped.get(q["id"], [])
        item = {
            "question_id": q["id"],
            "label": q["label"],
            "type": q["type"],
            "base": 0,
        }
        if q["type"] in ("single", "multiple"):
            item["options"] = json.loads(q["options"])
            counts = {option: 0 for option in item["options"]}
            base = 0
            for value in values:
                if q["type"] == "single" and isinstance(value, str) and value:
                    base += 1
                    counts[value] = counts.get(value, 0) + 1
                elif q["type"] == "multiple" and isinstance(value, list):
                    if value:
                        base += 1
                    for chosen in value:
                        if chosen in counts:
                            counts[chosen] += 1
            item["base"] = base
            item["counts"] = counts
            item["percent"] = {
                option: round(count / base * 100, 1) if base else 0 for option, count in counts.items()
            }
        elif q["type"] == "scale":
            numbers = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            item["base"] = len(numbers)
            item["min"] = q["scale_min"]
            item["max"] = q["scale_max"]
            item["mean"] = round(sum(numbers) / len(numbers), 2) if numbers else None
            item["distribution"] = {}
            if numbers:
                for value in numbers:
                    key = str(int(value) if float(value).is_integer() else value)
                    item["distribution"][key] = item["distribution"].get(key, 0) + 1
        else:
            texts = [v for v in values if isinstance(v, str) and v.strip()]
            item["base"] = len(texts)
            item["answers"] = texts[:20]
        result["questions"].append(item)
    return result


def stats_to_prompt(survey, stats):
    lines = [
        f"Исследование: {survey['title']}",
        f"Цель: {survey['goal'] or 'не указана'}",
        f"Задачи: {survey['tasks'] or 'не указаны'}",
        f"Всего анкет: {stats['total_responses']}",
        "",
    ]
    for item in stats["questions"]:
        lines.append(f"Вопрос: {item['label']} (тип: {item['type']}, база: {item['base']})")
        if item["type"] in ("single", "multiple"):
            for option, count in item["counts"].items():
                lines.append(f"  - {option}: {count} ({item['percent'][option]}%)")
        elif item["type"] == "scale":
            lines.append(f"  - среднее: {item['mean']}, диапазон: {item['min']}..{item['max']}")
            lines.append(f"  - распределение: {item['distribution']}")
        else:
            lines.append(f"  - открытых ответов: {item['base']}")
        lines.append("")
    return "\n".join(lines)


def local_analytics(survey, stats):
    parts = [f"Локальная аналитика по исследованию «{survey['title']}»."]
    parts.append(f"Собрано анкет: {stats['total_responses']}.")
    if stats["total_responses"] == 0:
        parts.append("Ответов пока нет, анализ будет содержательным после начала сбора данных.")
        return "\n\n".join(parts)
    for item in stats["questions"]:
        if item["type"] in ("single", "multiple"):
            if not item["counts"] or not item["base"]:
                parts.append(f"«{item['label']}»: нет распределения.")
                continue
            leader = max(item["counts"], key=item["counts"].get)
            share = item["percent"][leader]
            parts.append(
                f"«{item['label']}»: лидирует «{leader}» — {share}% от базы ({item['base']}); "
                f"распределение: "
                + ", ".join(f"{o} — {c}" for o, c in item["counts"].items())
                + "."
            )
        elif item["type"] == "scale":
            if item["mean"] is None:
                parts.append(f"«{item['label']}»: нет числовых ответов.")
            else:
                parts.append(
                    f"«{item['label']}»: среднее {item['mean']} при базе {item['base']} "
                    f"в диапазоне {item['min']}..{item['max']}."
                )
        else:
            parts.append(f"«{item['label']}»: получено открытых ответов — {item['base']}.")
    parts.append(
        "Это автоматический обзор распределений. Для интерпретаций с помощью ИИ настройте "
        "DEEPSEEK_API_KEY и запустите генерацию повторно."
    )
    return "\n\n".join(parts)


def deepseek_analytics(survey, stats, key):
    prompt = (
        "Ты — социолог-аналитик. Составь аналитическую записку на русском языке по "
        "агрегированным результатам опроса. Разделяй факты (данные) и интерпретации, "
        "укажи ограничения выводов и не делай утверждений, которых нет в данных. "
        "Если база вопроса слишком мала, отметь это.\n\n"
        + stats_to_prompt(survey, stats)
    )
    payload = json.dumps(
        {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты аккуратный социологический аналитик."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "Sociolab/1.0"
    failed_logins = {}
    lock = threading.Lock()

    def log_message(self, fmt, *args):
        print(f"[sociolab] {self.address_string()} {fmt % args}", file=sys.stderr)

    def db(self):
        return connect(DB_PATH)

    def set_cookie_value(self, token, max_age):
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"sociolab_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"

    def current_user(self, db):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("sociolab_session")
        if not morsel:
            return None
        token_hash = hashlib.sha256(morsel.value.encode()).hexdigest()
        row = db.execute(
            """SELECT u.id, u.name, u.email, u.role, u.is_active, s.expires_at
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ?""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if parse_iso(row["expires_at"]) < datetime.now(timezone.utc):
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            db.commit()
            return None
        if not row["is_active"]:
            return None
        return dict(row)

    def require(self, db, roles=None):
        user = self.current_user(db)
        if not user:
            raise ApiError(401, "Требуется вход в систему")
        if roles and user["role"] not in roles:
            raise ApiError(403, "Недостаточно прав")
        return user

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
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
        self.send_response(200)
        self.send_header("Content-Type", (mime or "application/octet-stream") + ("; charset=utf-8" if (mime or "").startswith("text") or (mime or "") == "application/javascript" else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_PUT(self):
        self.handle_request("PUT")

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
            self.dispatch(method, path)
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})
        except Exception as exc:  # noqa: BLE001
            print(f"[sociolab] Ошибка: {exc!r}", file=sys.stderr)
            self.send_json(500, {"error": "Внутренняя ошибка сервера"})

    def dispatch(self, method, path):
        db = self.db()
        try:
            result = self.api(db, method, path)
            if result is not None:
                status, payload, headers = result
                db.commit()
                self.send_json(status, payload, headers)
        finally:
            db.close()

    def api(self, db, method, path):
        if path == "/api/setup" and method == "GET":
            count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            return 200, {"needs_admin": count == 0}, None

        if path == "/api/session" and method == "GET":
            user = self.current_user(db)
            return 200, {"authenticated": bool(user), "user": user}, None

        if path == "/api/register" and method == "POST":
            return self.register(db)

        if path == "/api/login" and method == "POST":
            return self.login(db)

        if path == "/api/login/verify" and method == "POST":
            return self.login_verify(db)

        if path == "/api/logout" and method == "POST":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            if "sociolab_session" in cookie:
                token_hash = hashlib.sha256(cookie["sociolab_session"].value.encode()).hexdigest()
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return 200, {"ok": True}, {"Set-Cookie": self.set_cookie_value("", 0)}

        if path == "/api/users" and method == "GET":
            self.require(db, ("admin", "researcher"))
            rows = db.execute(
                "SELECT id, name, email, role, is_active, created_at FROM users ORDER BY name"
            ).fetchall()
            return 200, {"users": [dict(row) for row in rows]}, None

        if path == "/api/users" and method == "POST":
            return self.create_user(db)

        match = re.fullmatch(r"/api/users/(\d+)", path)
        if match and method in ("PATCH", "DELETE"):
            return self.update_user(db, int(match.group(1)), method)

        if path == "/api/surveys" and method == "GET":
            self.require(db)
            rows = db.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM questions q WHERE q.survey_id = s.id) AS question_count,
                          (SELECT COUNT(*) FROM responses r WHERE r.survey_id = s.id) AS response_count
                   FROM surveys s ORDER BY s.updated_at DESC"""
            ).fetchall()
            return 200, {"surveys": [self.survey_row(db, row) for row in rows]}, None

        if path == "/api/surveys" and method == "POST":
            user = self.require(db, ("admin", "researcher"))
            return self.create_survey(db, user)

        match = re.fullmatch(r"/api/surveys/(\d+)", path)
        if match:
            survey_id = int(match.group(1))
            if method == "GET":
                return self.get_survey(db, survey_id)
            if method == "PUT":
                return self.update_survey(db, survey_id)
            if method == "DELETE":
                return self.delete_survey(db, survey_id)

        match = re.fullmatch(r"/api/surveys/(\d+)/questions", path)
        if match and method == "PUT":
            return self.set_questions(db, int(match.group(1)))

        match = re.fullmatch(r"/api/surveys/(\d+)/members", path)
        if match:
            survey_id = int(match.group(1))
            if method == "GET":
                return self.list_members(db, survey_id)
            if method == "POST":
                return self.add_member(db, survey_id)

        match = re.fullmatch(r"/api/surveys/(\d+)/members/(\d+)", path)
        if match and method == "DELETE":
            return self.remove_member(db, int(match.group(1)), int(match.group(2)))

        match = re.fullmatch(r"/api/surveys/(\d+)/responses", path)
        if match:
            survey_id = int(match.group(1))
            if method == "GET":
                return self.list_responses(db, survey_id)
            if method == "POST":
                return self.submit_response(db, survey_id)

        match = re.fullmatch(r"/api/surveys/(\d+)/analytics", path)
        if match:
            survey_id = int(match.group(1))
            if method == "GET":
                return self.get_analytics(db, survey_id)
            if method == "POST":
                return self.run_analytics(db, survey_id)

        raise ApiError(404, "Не найдено")

    def survey_row(self, db, row):
        data = dict(row)
        data["questions"] = [
            self.question_dict(q)
            for q in db.execute(
                "SELECT * FROM questions WHERE survey_id = ? ORDER BY position, id", (row["id"],)
            ).fetchall()
        ]
        return data

    def question_dict(self, row):
        return {
            "id": row["id"],
            "label": row["label"],
            "type": row["type"],
            "required": bool(row["required"]),
            "options": json.loads(row["options"]),
            "scale_min": row["scale_min"],
            "scale_max": row["scale_max"],
            "scale_step": row["scale_step"],
        }

    def register(self, db):
        count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count > 0:
            raise ApiError(403, "Администратор уже создан. Обратитесь к администратору за учётной записью.")
        data = read_json(self)
        name = clean_text(data.get("name"), 120, required=True, field="имя")
        email = normalize_email(data.get("email"))
        password = clean_text(data.get("password"), 200, required=True, field="пароль")
        if len(password) < 8:
            raise ApiError(400, "Пароль должен содержать не менее 8 символов")
        cursor = db.execute(
            "INSERT INTO users (name, email, password, role, is_active, created_at) VALUES (?, ?, ?, 'admin', 1, ?)",
            (name, email, password_hash(password), now_iso()),
        )
        token = self.create_session(db, cursor.lastrowid)
        return 201, {"ok": True}, {"Set-Cookie": self.set_cookie_value(token, SESSION_TTL_SECONDS)}

    def login(self, db):
        data = read_json(self)
        email = normalize_email(data.get("email"))
        password = data.get("password") or ""
        address = self.client_address[0]
        with self.lock:
            attempts, until = self.failed_logins.get(address, (0, 0))
            if attempts >= 5 and time.time() < until:
                raise ApiError(429, "Слишком много неудачных попыток. Попробуйте позже.")
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not row["is_active"] or not check_password(password, row["password"]):
            with self.lock:
                attempts, _ = self.failed_logins.get(address, (0, 0))
                self.failed_logins[address] = (attempts + 1, time.time() + 300)
            raise ApiError(401, "Неверная почта или пароль")
        with self.lock:
            self.failed_logins.pop(address, None)

        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(challenge_token.encode()).hexdigest()
        code_hash = hashlib.sha256((challenge_token + ":" + code).encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)).isoformat()
        db.execute(
            """INSERT INTO login_challenges (token_hash, user_id, code_hash, attempts, expires_at, created_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (token_hash, row["id"], code_hash, expires, now_iso()),
        )
        db.commit()
        delivered = send_code_email(row["email"], code)
        payload = {"twofa_required": True, "challenge": challenge_token, "email": row["email"], "delivered": delivered}
        if ALLOW_DEV_CODES and not delivered:
            payload["dev_code"] = code
        return 200, payload, None

    def login_verify(self, db):
        data = read_json(self)
        challenge = clean_text(data.get("challenge"), 200, required=True, field="challenge")
        code = clean_text(data.get("code"), 12, required=True, field="код")
        token_hash = hashlib.sha256(challenge.encode()).hexdigest()
        row = db.execute("SELECT * FROM login_challenges WHERE token_hash = ?", (token_hash,)).fetchone()
        if not row:
            raise ApiError(400, "Сессия подтверждения не найдена. Войдите заново.")
        if parse_iso(row["expires_at"]) < datetime.now(timezone.utc):
            db.execute("DELETE FROM login_challenges WHERE id = ?", (row["id"],))
            raise ApiError(400, "Код истёк. Войдите заново.")
        if row["attempts"] >= 5:
            db.execute("DELETE FROM login_challenges WHERE id = ?", (row["id"],))
            raise ApiError(429, "Слишком много попыток. Войдите заново.")
        expected = hashlib.sha256((challenge + ":" + code).encode()).hexdigest()
        if not hmac.compare_digest(expected, row["code_hash"]):
            db.execute("UPDATE login_challenges SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            raise ApiError(401, "Неверный код подтверждения")
        user = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        db.execute("DELETE FROM login_challenges WHERE id = ?", (row["id"],))
        if not user or not user["is_active"]:
            raise ApiError(403, "Учётная запись недоступна")
        token = self.create_session(db, user["id"])
        return 200, {
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}
        }, {"Set-Cookie": self.set_cookie_value(token, SESSION_TTL_SECONDS)}

    def create_session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, expires, now_iso()),
        )
        db.commit()
        return token

    def create_user(self, db):
        self.require(db, ("admin",))
        data = read_json(self)
        name = clean_text(data.get("name"), 120, required=True, field="имя")
        email = normalize_email(data.get("email"))
        password = clean_text(data.get("password"), 200, required=True, field="пароль")
        role = data.get("role", "interviewer")
        if role not in ROLES:
            raise ApiError(400, "Недопустимая роль")
        if len(password) < 8:
            raise ApiError(400, "Пароль должен содержать не менее 8 символов")
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ApiError(409, "Пользователь с такой почтой уже существует")
        cursor = db.execute(
            "INSERT INTO users (name, email, password, role, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (name, email, password_hash(password), role, now_iso()),
        )
        return 201, {"id": cursor.lastrowid}, None

    def update_user(self, db, user_id, method):
        admin = self.require(db, ("admin",))
        if method == "DELETE":
            if user_id == admin["id"]:
                raise ApiError(400, "Нельзя удалить собственную учётную запись")
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return 200, {"ok": True}, None
        data = read_json(self)
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ApiError(404, "Пользователь не найден")
        role = data.get("role", row["role"])
        if role not in ROLES:
            raise ApiError(400, "Недопустимая роль")
        is_active = 1 if data.get("is_active", row["is_active"]) else 0
        if user_id == admin["id"] and (role != "admin" or not is_active):
            raise ApiError(400, "Нельзя понизить или отключить собственную учётную запись")
        db.execute("UPDATE users SET role = ?, is_active = ? WHERE id = ?", (role, is_active, user_id))
        return 200, {"ok": True}, None

    def create_survey(self, db, user):
        data = read_json(self)
        title = clean_text(data.get("title"), 200, required=True, field="название")
        description = clean_text(data.get("description"), 4000, field="описание")
        goal = clean_text(data.get("goal"), 2000, field="цель")
        tasks = clean_text(data.get("tasks"), 4000, field="задачи")
        stamp = now_iso()
        cursor = db.execute(
            """INSERT INTO surveys (title, description, goal, tasks, status, owner_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (title, description, goal, tasks, user["id"], stamp, stamp),
        )
        db.execute(
            "INSERT INTO survey_members (survey_id, user_id, created_at) VALUES (?, ?, ?)",
            (cursor.lastrowid, user["id"], stamp),
        )
        return 201, {"id": cursor.lastrowid}, None

    def get_survey(self, db, survey_id):
        self.require(db)
        row = db.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM questions q WHERE q.survey_id = s.id) AS question_count,
                      (SELECT COUNT(*) FROM responses r WHERE r.survey_id = s.id) AS response_count
               FROM surveys s WHERE s.id = ?""",
            (survey_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "Исследование не найдено")
        members = db.execute(
            """SELECT u.id, u.name, u.email, u.role FROM survey_members m
               JOIN users u ON u.id = m.user_id WHERE m.survey_id = ? ORDER BY u.name""",
            (survey_id,),
        ).fetchall()
        data = self.survey_row(db, row)
        data["members"] = [dict(member) for member in members]
        return 200, {"survey": data}, None

    def update_survey(self, db, survey_id):
        self.require(db, ("admin", "researcher"))
        data = read_json(self)
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if not row:
            raise ApiError(404, "Исследование не найдено")
        title = clean_text(data.get("title", row["title"]), 200, required=True, field="название")
        description = clean_text(data.get("description", row["description"]), 4000, field="описание")
        goal = clean_text(data.get("goal", row["goal"]), 2000, field="цель")
        tasks = clean_text(data.get("tasks", row["tasks"]), 4000, field="задачи")
        status = data.get("status", row["status"])
        allowed = ("draft", "field", "processing", "done")
        if status not in allowed:
            raise ApiError(400, "Недопустимый статус")
        db.execute(
            """UPDATE surveys SET title = ?, description = ?, goal = ?, tasks = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (title, description, goal, tasks, status, now_iso(), survey_id),
        )
        return 200, {"ok": True}, None

    def delete_survey(self, db, survey_id):
        self.require(db, ("admin",))
        db.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
        return 200, {"ok": True}, None

    def validate_questions(self, raw):
        if not isinstance(raw, list):
            raise ApiError(400, "Список вопросов должен быть массивом")
        if len(raw) > 200:
            raise ApiError(400, "Слишком много вопросов")
        cleaned = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ApiError(400, "Некорректный вопрос")
            label = clean_text(item.get("label"), 500, required=True, field=f"вопрос {index + 1}")
            qtype = item.get("type")
            if qtype not in QUESTION_TYPES:
                raise ApiError(400, f"Недопустимый тип вопроса: {qtype}")
            question = {
                "label": label,
                "type": qtype,
                "required": bool(item.get("required")),
                "options": [],
                "scale_min": None,
                "scale_max": None,
                "scale_step": None,
            }
            if qtype in ("single", "multiple"):
                options = item.get("options")
                if not isinstance(options, list) or len(options) < 2:
                    raise ApiError(400, f"Вопрос «{label}» требует минимум два варианта")
                seen = []
                for option in options:
                    text = clean_text(option, 200, required=True, field=f"вариант в «{label}»")
                    if text in seen:
                        raise ApiError(400, f"Варианты в «{label}» не должны повторяться")
                    seen.append(text)
                question["options"] = seen
            if qtype == "scale":
                try:
                    scale_min = float(item.get("scale_min"))
                    scale_max = float(item.get("scale_max"))
                except (TypeError, ValueError):
                    raise ApiError(400, f"Для вопроса «{label}» укажите числовые границы")
                if scale_max <= scale_min:
                    raise ApiError(400, f"Верхняя граница «{label}» должна быть больше нижней")
                step = item.get("scale_step")
                if step in (None, ""):
                    step = 1
                try:
                    step = float(step)
                except (TypeError, ValueError):
                    raise ApiError(400, f"Некорректный шаг шкалы в «{label}»")
                if step <= 0:
                    raise ApiError(400, f"Шаг шкалы в «{label}» должен быть положительным")
                question["scale_min"] = scale_min
                question["scale_max"] = scale_max
                question["scale_step"] = step
            cleaned.append(question)
        return cleaned

    def set_questions(self, db, survey_id):
        self.require(db, ("admin", "researcher"))
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if not row:
            raise ApiError(404, "Исследование не найдено")
        collected = db.execute(
            "SELECT COUNT(*) AS c FROM responses WHERE survey_id = ?", (survey_id,)
        ).fetchone()["c"]
        if collected:
            raise ApiError(409, "Нельзя менять анкету после начала сбора ответов")
        data = read_json(self)
        questions = self.validate_questions(data.get("questions"))
        db.execute("DELETE FROM questions WHERE survey_id = ?", (survey_id,))
        for position, question in enumerate(questions):
            db.execute(
                """INSERT INTO questions (survey_id, position, label, type, required, options, scale_min, scale_max, scale_step)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    survey_id,
                    position,
                    question["label"],
                    question["type"],
                    1 if question["required"] else 0,
                    json.dumps(question["options"], ensure_ascii=False),
                    question["scale_min"],
                    question["scale_max"],
                    question["scale_step"],
                ),
            )
        db.execute("UPDATE surveys SET updated_at = ? WHERE id = ?", (now_iso(), survey_id))
        return 200, {"ok": True}, None

    def list_members(self, db, survey_id):
        self.require(db)
        rows = db.execute(
            """SELECT u.id, u.name, u.email, u.role FROM survey_members m
               JOIN users u ON u.id = m.user_id WHERE m.survey_id = ? ORDER BY u.name""",
            (survey_id,),
        ).fetchall()
        return 200, {"members": [dict(row) for row in rows]}, None

    def add_member(self, db, survey_id):
        self.require(db, ("admin", "researcher"))
        data = read_json(self)
        user_id = data.get("user_id")
        if not isinstance(user_id, int):
            raise ApiError(400, "Некорректный идентификатор сотрудника")
        if not db.execute("SELECT 1 FROM surveys WHERE id = ?", (survey_id,)).fetchone():
            raise ApiError(404, "Исследование не найдено")
        if not db.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone():
            raise ApiError(404, "Сотрудник не найден")
        db.execute(
            "INSERT OR IGNORE INTO survey_members (survey_id, user_id, created_at) VALUES (?, ?, ?)",
            (survey_id, user_id, now_iso()),
        )
        return 200, {"ok": True}, None

    def remove_member(self, db, survey_id, user_id):
        self.require(db, ("admin", "researcher"))
        db.execute(
            "DELETE FROM survey_members WHERE survey_id = ? AND user_id = ?", (survey_id, user_id)
        )
        return 200, {"ok": True}, None

    def submit_response(self, db, survey_id):
        user = self.require(db)
        survey = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if not survey:
            raise ApiError(404, "Исследование не найдено")
        data = read_json(self)
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise ApiError(400, "Ожидается объект ответов")
        questions = db.execute(
            "SELECT * FROM questions WHERE survey_id = ? ORDER BY position, id", (survey_id,)
        ).fetchall()
        if not questions:
            raise ApiError(409, "В анкете пока нет вопросов")
        prepared = []
        for question in questions:
            value = answers.get(str(question["id"]), answers.get(question["id"]))
            qtype = question["type"]
            options = json.loads(question["options"])
            if qtype == "single":
                if value in (None, ""):
                    if question["required"]:
                        raise ApiError(400, f"Вопрос «{question['label']}» обязателен")
                    prepared.append((question["id"], None))
                    continue
                if value not in options:
                    raise ApiError(400, f"Недопустимый вариант в «{question['label']}»")
                prepared.append((question["id"], value))
            elif qtype == "multiple":
                chosen = value if isinstance(value, list) else []
                chosen = [item for item in chosen if item in options]
                if not chosen and question["required"]:
                    raise ApiError(400, f"Вопрос «{question['label']}» обязателен")
                prepared.append((question["id"], chosen))
            elif qtype == "scale":
                if value in (None, ""):
                    if question["required"]:
                        raise ApiError(400, f"Вопрос «{question['label']}» обязателен")
                    prepared.append((question["id"], None))
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise ApiError(400, f"Вопрос «{question['label']}» ожидает число")
                if number < question["scale_min"] or number > question["scale_max"]:
                    raise ApiError(400, f"Значение «{question['label']}» вне шкалы")
                prepared.append((question["id"], number))
            else:
                text = "" if value is None else str(value).strip()
                if not text and question["required"]:
                    raise ApiError(400, f"Вопрос «{question['label']}» обязателен")
                if len(text) > 5000:
                    raise ApiError(400, f"Ответ на «{question['label']}» слишком длинный")
                prepared.append((question["id"], text or None))
        cursor = db.execute(
            "INSERT INTO responses (survey_id, user_id, source, created_at) VALUES (?, ?, 'manual', ?)",
            (survey_id, user["id"], now_iso()),
        )
        for question_id, value in prepared:
            db.execute(
                "INSERT INTO answers (response_id, question_id, value) VALUES (?, ?, ?)",
                (cursor.lastrowid, question_id, json.dumps(value, ensure_ascii=False)),
            )
        return 201, {"id": cursor.lastrowid}, None

    def list_responses(self, db, survey_id):
        user = self.require(db)
        if user["role"] == "interviewer":
            raise ApiError(403, "Недостаточно прав")
        rows = db.execute(
            """SELECT r.id, r.source, r.created_at, u.name AS author
               FROM responses r LEFT JOIN users u ON u.id = r.user_id
               WHERE r.survey_id = ? ORDER BY r.id DESC LIMIT 500""",
            (survey_id,),
        ).fetchall()
        return 200, {"responses": [dict(row) for row in rows]}, None

    def get_analytics(self, db, survey_id):
        self.require(db)
        if not db.execute("SELECT 1 FROM surveys WHERE id = ?", (survey_id,)).fetchone():
            raise ApiError(404, "Исследование не найдено")
        stats = build_stats(db, survey_id)
        latest = db.execute(
            "SELECT source, content, created_at FROM analyses WHERE survey_id = ? ORDER BY id DESC LIMIT 1",
            (survey_id,),
        ).fetchone()
        return 200, {
            "stats": stats,
            "analysis": dict(latest) if latest else None,
            "ai_enabled": bool(deepseek_key()),
        }, None

    def run_analytics(self, db, survey_id):
        self.require(db, ("admin", "researcher"))
        survey = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if not survey:
            raise ApiError(404, "Исследование не найдено")
        stats = build_stats(db, survey_id)
        key = deepseek_key()
        source = "local"
        if key:
            try:
                content = deepseek_analytics(dict(survey), stats, key)
                source = "deepseek"
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as exc:
                print(f"[sociolab] DeepSeek недоступен: {exc!r}", file=sys.stderr)
                content = local_analytics(dict(survey), stats)
                source = "local"
        else:
            content = local_analytics(dict(survey), stats)
        db.execute(
            "INSERT INTO analyses (survey_id, source, content, created_at) VALUES (?, ?, ?, ?)",
            (survey_id, source, content, now_iso()),
        )
        return 200, {"analysis": {"source": source, "content": content, "created_at": now_iso()}}, None


def create_server(host=HOST, port=PORT, path=None):
    global DB_PATH
    if path:
        DB_PATH = Path(path).resolve()
        init_db(DB_PATH).close()
    return ThreadingHTTPServer((host, port), Handler)


def main():
    db = init_db(DB_PATH)
    count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    db.close()
    print(f"[sociolab] База данных: {DB_PATH}")
    print(f"[sociolab] SMTP: {'настроен' if SMTP_HOST else 'НЕ настроен (код печатается в консоль)'}")
    if count == 0:
        print("[sociolab] Откройте приложение и создайте первую учётную запись администратора.")
    print(f"[sociolab] Сервер: http://{HOST}:{PORT}")
    server = create_server(HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sociolab] Остановка")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
