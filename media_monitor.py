"""Telegram public-channel monitoring and deterministic media analysis."""

import html
import json
import os
import asyncio
import re
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock


AGENTS = ("Telegram · публичные каналы", "Telegram · посты", "Telegram · комментарии", "Аналитик · тональность и тренды", "ИИ · подробный аналитический отчёт")
DEFAULT_LOOKBACK_HOURS = 24
MAX_LOOKBACK_HOURS = 720
TELEGRAM_LOCK = Lock()
TELEGRAM_AUTH = {}
ROOT = Path(__file__).resolve().parent
STOP = {"это", "как", "что", "для", "или", "при", "про", "the", "and", "with", "from", "сми", "новости"}
COUNTRIES = {
    "US": ("США", "en", "United States", "США", "United States", "USA"),
    "GB": ("Великобритания", "en", "United Kingdom", "Великобритания", "Britain", "UK"),
    "FR": ("Франция", "fr", "France", "Франция", "France"),
    "DE": ("Германия", "de", "Germany", "Германия", "Germany", "Deutschland"),
    "RU": ("Россия", "ru", "Russia", "Россия", "Russia"),
    "UA": ("Украина", "uk", "Ukraine", "Украина", "Ukraine"),
    "CN": ("Китай", "zh-CN", "China", "Китай", "China"),
    "IN": ("Индия", "en", "India", "Индия", "India"),
    "JP": ("Япония", "ja", "Japan", "Япония", "Japan"),
    "BR": ("Бразилия", "pt-BR", "Brazil", "Бразилия", "Brazil"),
    "TR": ("Турция", "tr", "Turkey", "Турция", "Turkey", "Türkiye"),
    "IL": ("Израиль", "he", "Israel", "Израиль", "Israel"),
    "IR": ("Иран", "fa", "Iran", "Иран", "Iran"),
    "KR": ("Южная Корея", "ko", "South Korea", "Южная Корея", "South Korea"),
    "MX": ("Мексика", "es-419", "Mexico", "Мексика", "Mexico"),
    "ZA": ("ЮАР", "en", "South Africa", "ЮАР", "South Africa"),
}
# Approximate marker positions for a compact narrative map (longitude, latitude).
MAP_COORDS = {
    "US": (-100, 38), "GB": (-3, 55), "FR": (2, 46), "DE": (10, 51), "RU": (90, 60),
    "UA": (32, 49), "CN": (105, 35), "IN": (79, 22), "JP": (138, 36), "BR": (-52, -10),
    "TR": (35, 39), "IL": (35, 31), "IR": (53, 32), "KR": (127, 36), "MX": (-102, 23), "ZA": (24, -30),
}
TOPICS = {
    "Конфликты и безопасность": ("war", "attack", "strike", "military", "missile", "войн", "атак", "удар", "военн", "ракета", "guerre", "krieg"),
    "Политика и дипломатия": ("election", "government", "minister", "diplomat", "sanction", "выбор", "правитель", "министр", "санкци", "president"),
    "Экономика": ("econom", "inflation", "trade", "market", "эконом", "инфляц", "торгов", "рынок", "bank"),
    "Общество и здоровье": ("health", "school", "education", "protest", "здоров", "школ", "образован", "протест", "hospital"),
    "Климат и экология": ("climate", "environment", "flood", "weather", "климат", "эколог", "наводнен", "погод"),
    "Технологии": ("technology", "artificial intelligence", "software", "технолог", "искусственн", "нейросет", "tech"),
}
HOSTILITY = ("attack", "strike", "threat", "war against", "hostile", "санкци", "атак", "удар", "угроз", "напад", "bomb", "retaliat", "ответн", "возмезд", "revenge")
RETALIATION = ("retaliat", "ответн", "возмезд", "revenge", "reprisal", "отмест", "counterattack")
DIRECTED_ACTION = re.compile(r"\b(?:attacks?|strikes?|threatens?|retaliates? against|санкционирует|атакует|угрожает|нанес(?:ла|ли)? удар по)\b", re.I)


def clean_markup(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def safe_url(value):
    if not isinstance(value, str) or len(value) > 1800:
        return None
    parts = urllib.parse.urlsplit(value)
    return value if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password else None


def telegram_configured():
    return bool(os.environ.get("TELEGRAM_API_ID", "").strip() and os.environ.get("TELEGRAM_API_HASH", "").strip())


def telegram_session_name():
    return os.environ.get("TELEGRAM_SESSION_FILE", str(ROOT / "data" / "telegram.session"))


def telegram_proxy():
    """Optional SOCKS5 transport for hosts without direct MTProto access."""
    host = os.environ.get("TELEGRAM_PROXY_HOST", "").strip()
    port = os.environ.get("TELEGRAM_PROXY_PORT", "").strip()
    if not host and not port:
        return None
    if not host or any(char.isspace() for char in host) or not port:
        raise ValueError("Укажите TELEGRAM_PROXY_HOST и TELEGRAM_PROXY_PORT для SOCKS5")
    try:
        port_number = int(port)
    except ValueError as exc:
        raise ValueError("TELEGRAM_PROXY_PORT должен быть числом от 1 до 65535") from exc
    if not 1 <= port_number <= 65535:
        raise ValueError("TELEGRAM_PROXY_PORT должен быть числом от 1 до 65535")
    username = os.environ.get("TELEGRAM_PROXY_USER", "").strip()
    password = os.environ.get("TELEGRAM_PROXY_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("Для SOCKS5 задайте и TELEGRAM_PROXY_USER, и TELEGRAM_PROXY_PASSWORD")
    proxy = {"proxy_type": "socks5", "addr": host, "port": port_number, "rdns": True}
    if username:
        proxy.update({"username": username, "password": password})
    return proxy


def _telegram_client():
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise ValueError("Telethon не установлен. Добавьте зависимость telethon") from exc
    if not telegram_configured():
        raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH не настроены")
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID должен быть числом") from exc
    session = os.environ.get("TELEGRAM_SESSION", "").strip() or telegram_session_name()
    Path(session).parent.mkdir(parents=True, exist_ok=True)
    proxy = telegram_proxy()
    return TelegramClient(session, api_id, os.environ["TELEGRAM_API_HASH"].strip(), **({"proxy": proxy} if proxy else {}))


def _telegram_run(operation):
    with TELEGRAM_LOCK:
        return asyncio.run(operation())


async def _telegram_authorized(client):
    try:
        await client.connect()
        return await client.is_user_authorized()
    finally:
        await client.disconnect()


def telegram_status():
    if not telegram_configured():
        return {"configured": False, "authorized": False, "message": "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH"}
    if not TELEGRAM_LOCK.acquire(blocking=False):
        return {"configured": True, "authorized": False, "message": "Telegram занят. Повторите проверку позже"}
    try:
        authorized = asyncio.run(asyncio.wait_for(_telegram_authorized(_telegram_client()), timeout=12))
    except TimeoutError:
        return {"configured": True, "authorized": False, "message": "Таймаут подключения к Telegram. Проверьте доступ сервера к Telegram"}
    except Exception as exc:
        return {"configured": True, "authorized": False, "message": str(exc)[:180]}
    finally:
        TELEGRAM_LOCK.release()
    return {"configured": True, "authorized": authorized, "message": "Готово" if authorized else "Требуется авторизация"}


async def _telegram_start_auth(phone):
    client = _telegram_client()
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    finally:
        await client.disconnect()
    TELEGRAM_AUTH.update({"phone": phone, "phone_code_hash": sent.phone_code_hash})
    return {"ok": True, "password_required": False}


def telegram_start_auth(phone):
    phone = str(phone or "").strip()
    if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
        raise ValueError("Укажите номер телефона в международном формате, например +79991234567")
    return _telegram_run(lambda: _telegram_start_auth(phone))


async def _telegram_verify(code, password=""):
    from telethon.errors import SessionPasswordNeededError
    if not TELEGRAM_AUTH.get("phone_code_hash"):
        raise ValueError("Сначала запросите код подтверждения")
    client = _telegram_client()
    await client.connect()
    try:
        if code:
            await client.sign_in(TELEGRAM_AUTH["phone"], code, phone_code_hash=TELEGRAM_AUTH["phone_code_hash"])
        elif not password:
            raise ValueError("Введите код подтверждения")
    except SessionPasswordNeededError:
        if not password:
            await client.disconnect()
            return {"ok": False, "password_required": True}
        await client.sign_in(password=password)
    authorized = await client.is_user_authorized()
    await client.disconnect()
    if not authorized:
        raise ValueError("Telegram не подтвердил вход")
    TELEGRAM_AUTH.clear()
    return {"ok": True, "password_required": False}


def telegram_verify(code="", password=""):
    return _telegram_run(lambda: _telegram_verify(str(code or "").strip(), str(password or "")))


def _telegram_username(value):
    text = str(value or "").strip()
    parsed = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
    if parsed.hostname not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
        return text.lstrip("@").split("/")[0]
    path = parsed.path.strip("/").split("/")
    if not path or path[0].startswith("+") or path[0] in {"joinchat", "c"}:
        return ""
    return path[0].lstrip("@").split("?")[0]


async def _telegram_search(question, region=""):
    client = _telegram_client()
    await client.connect()
    try:
        from telethon import functions, types
        queries = []
        for query in (question, region, f"{question} {region}"):
            query = str(query or "").strip()
            if query and query not in queries:
                queries.append(query)
        candidates = {}
        for query in queries:
            result = await client(functions.contacts.SearchRequest(q=query, limit=20))
            for chat in result.chats:
                if not isinstance(chat, (types.Channel, types.Chat)):
                    continue
                username = getattr(chat, "username", "")
                if not username or not getattr(chat, "broadcast", True):
                    continue
                candidates[username.lower()] = {"username": username, "title": getattr(chat, "title", username),
                    "link": f"https://t.me/{username}", "members": getattr(chat, "participants_count", None)}
            if len(candidates) >= 20:
                break
        return list(candidates.values())[:20]
    finally:
        await client.disconnect()


def telegram_search(question, region=""):
    return _telegram_run(lambda: _telegram_search(question, region))


async def _telegram_subscribe(channels):
    from telethon import functions
    client = _telegram_client()
    await client.connect()
    result = []
    try:
        for raw in channels:
            username = _telegram_username(raw)
            if not username or not re.fullmatch(r"[A-Za-z0-9_]{4,64}", username):
                continue
            try:
                await client(functions.channels.JoinChannelRequest(username))
                result.append({"username": username, "status": "subscribed"})
            except Exception as exc:
                result.append({"username": username, "status": "error", "message": str(exc)[:120]})
    finally:
        await client.disconnect()
    return result


def telegram_subscribe(channels):
    if not isinstance(channels, list) or not channels:
        raise ValueError("Выберите хотя бы один Telegram-канал")
    return _telegram_run(lambda: _telegram_subscribe(channels))


def _message_date(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def _telegram_agent(question, region="", channels=(), lookback_hours=DEFAULT_LOOKBACK_HOURS):
    """Read public posts and comments published within the requested lookback window."""
    try:
        hours = max(1, min(MAX_LOOKBACK_HOURS, int(lookback_hours)))
    except (TypeError, ValueError):
        hours = DEFAULT_LOOKBACK_HOURS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    client = _telegram_client()
    await client.connect()
    records = []
    try:
        for raw in channels:
            username = _telegram_username(raw)
            if not username:
                continue
            try:
                entity = await client.get_entity(username)
            except Exception:
                continue
            title = clean_markup(getattr(entity, "title", username))[:180]
            public_name = getattr(entity, "username", None) or username
            async for message in client.iter_messages(entity, limit=200):
                message_date = _message_date(getattr(message, "date", None))
                if message_date and message_date < cutoff:
                    break
                text = clean_markup(getattr(message, "message", ""))[:900]
                message_id = getattr(message, "id", 0)
                if not text or not message_id:
                    continue
                url = f"https://t.me/{public_name}/{message_id}"
                records.append({"source": "telegram", "title": text[:180], "excerpt": text, "url": url,
                                "published": str(getattr(message, "date", ""))[:100], "item_type": "post",
                                "community": title, "region": region[:180], "author": title,
                                "engagement": int(getattr(message, "views", 0) or 0) + int(getattr(message, "forwards", 0) or 0)})
                try:
                    async for comment in client.iter_messages(entity, reply_to=message_id, limit=100):
                        comment_date = _message_date(getattr(comment, "date", None))
                        if comment_date and comment_date < cutoff:
                            break
                        comment_text = clean_markup(getattr(comment, "message", ""))[:900]
                        comment_id = getattr(comment, "id", 0)
                        if comment_text and comment_id:
                            records.append({"source": "telegram", "title": comment_text[:180], "excerpt": comment_text,
                                            "url": url + "?comment=" + str(comment_id),
                                            "published": str(getattr(comment, "date", ""))[:100], "item_type": "comment",
                                            "community": title, "region": region[:180], "author": "Telegram user",
                                            "engagement": int(getattr(comment, "views", 0) or 0)})
                except Exception:
                    continue
    finally:
        await client.disconnect()
    return records


def telegram_agent(question, region="", channels=(), lookback_hours=DEFAULT_LOOKBACK_HOURS):
    return _telegram_run(lambda: _telegram_agent(question, region, channels, lookback_hours))


def summarize(rows):
    """Describe observed Telegram coverage and cite evidence; no author de-anonymisation."""
    rows = [dict(row) for row in rows]
    counts = Counter(row["source"] for row in rows)
    words = Counter(word for row in rows for word in set(re.findall(r"[\wа-яё]{4,}", row["title"].lower()))
                    if word not in STOP and not word.isdigit())
    def overview(items):
        themes = []
        for label, stems in TOPICS.items():
            matching = [item for item in items if any(stem in (item["title"] + " " + item.get("excerpt", "")).lower() for stem in stems)]
            if matching:
                themes.append({"name": label, "count": len(matching), "examples": [item["url"] for item in matching[:3] if item.get("url")]})
        themes.sort(key=lambda theme: -theme["count"])
        trending = []
        if len(items) >= 6:
            ordered = sorted(items, key=lambda item: item.get("id", 0))
            earlier, recent = ordered[:len(items) // 2], ordered[len(items) // 2:]
            def keywords(group):
                return Counter(word for item in group for word in set(re.findall(r"[\wа-яё]{4,}", item["title"].lower()))
                               if word not in STOP and not word.isdigit())
            before, after = keywords(earlier), keywords(recent)
            trending = [{"word": word, "recent": count, "previous": before[word]} for word, count in after.items()
                        if count >= 2 and count / len(recent) > before[word] / len(earlier)]
            trending.sort(key=lambda term: (term["recent"] / len(recent) - term["previous"] / len(earlier), term["recent"]), reverse=True)
        evidence = []
        for item in items:
            if len(evidence) >= 8:
                break
            text = (item["title"] + " " + item.get("excerpt", "")).lower()
            if not any(term in text for term in HOSTILITY) or not item.get("url"):
                continue
            targets = [info[0] for info in COUNTRIES.values() if any(re.search(r"(?<!\w)" + re.escape(alias.lower()) + r"(?!\w)", text) for alias in info[3:])]
            if targets:
                direction = None
                # Only describe a direction when a short explicit actor -> verb -> target phrase is present.
                matched = [(code, info, [alias for alias in info[3:] if re.search(r"(?<!\w)" + re.escape(alias.lower()) + r"(?!\w)", text)])
                           for code, info in COUNTRIES.items()]
                for actor_code, actor, actor_aliases in matched:
                    if not actor_aliases:
                        continue
                    for target_code, target, target_aliases in matched:
                        if actor_code == target_code:
                            continue
                        for actor_alias in actor_aliases:
                            for target_alias in target_aliases:
                                pattern = (r"(?<!\w)" + re.escape(actor_alias) + r"(?!\w).{0,35}?"
                                           + DIRECTED_ACTION.pattern + r".{0,35}?(?<!\w)" + re.escape(target_alias) + r"(?!\w)")
                                if re.search(pattern, text, re.I):
                                    direction = {"actor_mentioned": actor[0], "target_mentioned": target[0]}
                                    break
                            if direction:
                                break
                        if direction:
                            break
                    if direction:
                        break
                evidence.append({"title": item["title"], "url": item["url"], "targets_mentioned": targets,
                                 "direction": direction,
                                 "kind": "ответные действия" if any(term in text for term in RETALIATION) else "конфликтная риторика"})
        return {"themes": themes[:6], "trending_terms": trending[:6], "conflict_mentions": evidence[:8],
                "description": (f"Найдено {len(items)} публикаций; ведущие темы: " + ", ".join(t["name"] for t in themes[:3]) + ".") if items else "Публикаций пока нет."}

    narrative_counts = Counter(theme["name"] for theme in overview(rows)["themes"])
    narratives = [{"name": name, "count": count, "share": round(count / len(rows) * 100, 1) if rows else 0}
                  for name, count in narrative_counts.most_common()]
    SOCIAL_GROUPS = {
        "Страхи": ("страш", "боят", "опасн", "угроз", "страдан", "fear", "afraid", "danger", "worry"),
        "Потребности": ("нужн", "требу", "хотим", "нужда", "need", "want", "require", "support"),
        "Недовольство": ("плох", "ужас", "ненавиж", "долг", "коррупц", "bad", "hate", "corrupt", "expensive"),
        "Решения и инициативы": ("предлаг", "решен", "помощ", "инициатив", "вместе", "solution", "help", "initiative"),
    }
    social_analysis = []
    for label, stems in SOCIAL_GROUPS.items():
        hits = sum(1 for row in rows if row.get("source") == "telegram" and any(stem in (row["title"] + " " + row.get("excerpt", "")).lower() for stem in stems))
        if hits:
            social_analysis.append({"name": label, "count": hits, "share": round(hits / max(1, counts["telegram"]) * 100, 1)})
    positive = ("хорош", "поддерж", "успех", "помог", "спас", "рад", "справил", "great", "support", "success", "help")
    negative = ("плох", "страш", "опасн", "ненавиж", "проблем", "жалоб", "угроз", "ужас", "bad", "fear", "danger", "problem", "hate")
    sentiment = Counter()
    for row in rows:
        text = (row.get("title", "") + " " + row.get("excerpt", "")).lower()
        pos, neg = sum(text.count(word) for word in positive), sum(text.count(word) for word in negative)
        sentiment["positive" if pos > neg else "negative" if neg > pos else "neutral"] += 1
    return {"total": len(rows), "telegram": counts["telegram"],
            "terms": [{"word": word, "count": count} for word, count in words.most_common(10)],
            "overall": overview(rows), "narratives": narratives, "social_analysis": social_analysis,
            "communities": Counter(row.get("community", "") for row in rows if row.get("community") and row.get("source") == "telegram").most_common(10),
            "sentiment": {"positive": sentiment["positive"], "neutral": sentiment["neutral"], "negative": sentiment["negative"]},
            "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def collect(db, monitor, fetch_telegram=telegram_agent):
    """Collect Telegram posts and comments for the lookback window; previous observations stay available."""
    results = {}
    new_count = 0
    region = str(monitor.get("region", "")).strip()
    try:
        telegram_channels = json.loads(monitor.get("telegram_channels") or "[]")
    except (TypeError, ValueError):
        telegram_channels = []
    try:
        lookback = max(1, min(MAX_LOOKBACK_HOURS, int(monitor.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS)))
    except (TypeError, ValueError):
        lookback = DEFAULT_LOOKBACK_HOURS
    if not telegram_channels:
        results["telegram"] = "Не выбраны Telegram-каналы"
    else:
        try:
            records = fetch_telegram(monitor["question"], region, telegram_channels, lookback)
            if not isinstance(records, list):
                raise ValueError("Источник вернул некорректный список")
            collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for record in records[:500]:
                if not isinstance(record, dict):
                    continue
                link = safe_url(record.get("url"))
                if not link or record.get("source") != "telegram":
                    continue
                cursor = db.execute("""INSERT OR IGNORE INTO media_items
                    (monitor_id, source, country, title, excerpt, url, published, collected_at, item_type, author, community, engagement)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (monitor["id"], "telegram", "",
                    str(record.get("title", ""))[:350], str(record.get("excerpt", ""))[:900],
                    link, str(record.get("published", ""))[:100], collected_at,
                    str(record.get("item_type", "publication"))[:30], str(record.get("author", ""))[:180],
                    str(record.get("community", ""))[:180], max(0, int(record.get("engagement", 0) or 0))))
                new_count += cursor.rowcount
            results["telegram"] = f"Добавлено: {new_count}"
        except Exception as exc:
            results["telegram"] = "Ошибка источника: " + str(exc)[:140]
    results["social"] = f"telegram: {results.get('telegram', 'нет данных')}"
    return new_count, results
