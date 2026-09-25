"""Bounded public-source collectors and deterministic media analysis."""

import html
import json
import os
import asyncio
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


MAX_RESPONSE = 1024 * 1024
AGENTS = ("VK · публичные сообщества", "VK · посты и комментарии", "Telegram · публичные каналы", "Telegram · посты и комментарии", "Аналитик · тональность и тренды", "ИИ · подробный аналитический отчёт")
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


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MLSI-MediaMonitor/1.0 (public RSS/API)"})
    with urllib.request.urlopen(request, timeout=12) as response:
        content = response.read(MAX_RESPONSE + 1)
    if len(content) > MAX_RESPONSE:
        raise ValueError("Источник вернул слишком большой ответ")
    return content


def vk_api(method, params):
    token = os.environ.get("VK_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError("VK_ACCESS_TOKEN не настроен")
    query = {**params, "access_token": token, "v": "5.199"}
    data = json.loads(fetch("https://api.vk.com/method/" + method + "?" + urllib.parse.urlencode(query)))
    if not isinstance(data, dict) or "error" in data:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        raise ValueError("VK API: " + str(error.get("error_msg", "неожиданный ответ"))[:120])
    return data.get("response", {})


def telegram_configured():
    return bool(os.environ.get("TELEGRAM_API_ID", "").strip() and os.environ.get("TELEGRAM_API_HASH", "").strip())


def telegram_session_name():
    return os.environ.get("TELEGRAM_SESSION_FILE", str(ROOT / "data" / "telegram.session"))


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
    return TelegramClient(session, api_id, os.environ["TELEGRAM_API_HASH"].strip())


def _telegram_run(operation):
    with TELEGRAM_LOCK:
        return asyncio.run(operation())


async def _telegram_authorized(client):
    await client.connect()
    authorized = await client.is_user_authorized()
    await client.disconnect()
    return authorized


def telegram_status():
    if not telegram_configured():
        return {"configured": False, "authorized": False, "message": "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH"}
    try:
        authorized = _telegram_run(lambda: _telegram_authorized(_telegram_client()))
    except (ValueError, OSError) as exc:
        return {"configured": True, "authorized": False, "message": str(exc)[:180]}
    return {"configured": True, "authorized": authorized, "message": "Готово" if authorized else "Требуется авторизация"}


async def _telegram_start_auth(phone):
    client = _telegram_client()
    await client.connect()
    sent = await client.send_code_request(phone)
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


async def _telegram_agent(question, region="", channels=()):
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
            async for message in client.iter_messages(entity, limit=20):
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
                    async for comment in client.iter_messages(entity, reply_to=message_id, limit=20):
                        comment_text = clean_markup(getattr(comment, "message", ""))[:900]
                        comment_id = getattr(comment, "id", 0)
                        if comment_text and comment_id:
                            records.append({"source": "telegram", "title": comment_text[:180], "excerpt": comment_text,
                                            "url": url + "?comment=" + str(comment_id),
                                            "published": str(getattr(comment, "date", ""))[:100], "item_type": "comment",
                                            "community": title, "region": region[:180], "author": "Telegram user",
                                            "engagement": int(getattr(comment, "views", 0) or 0)})
                except Exception:
                    pass
    finally:
        await client.disconnect()
    return records


def telegram_agent(question, region="", channels=()):
    return _telegram_run(lambda: _telegram_agent(question, region, channels))


def vk_agent(question, region="", group_links=()):
    """Collect public VK communities, posts and a bounded sample of comments."""
    queries = []
    for query in (region, question, " ".join(part for part in (question, region) if part)):
        query = str(query or "").strip()
        if query and query not in queries:
            queries.append(query)
    groups = []
    seen_groups = set()
    for link in group_links or ():
        path = urllib.parse.urlsplit(str(link)).path.strip("/")
        path = re.sub(r"^(?:club|public|group)/?", "", path, flags=re.I)
        match = re.fullmatch(r"(\d+)", path)
        if match:
            group_id = int(match.group(1))
            if group_id not in seen_groups:
                seen_groups.add(group_id)
                groups.append({"id": group_id, "screen_name": path, "name": f"VK group {group_id}"})
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,80}", path):
            try:
                found = vk_api("groups.getById", {"group_ids": path}).get("groups", [])
            except (ValueError, OSError, TypeError, json.JSONDecodeError):
                found = []
            for group in found:
                group_id = int(group.get("id", 0))
                if group_id and group_id not in seen_groups:
                    seen_groups.add(group_id)
                    groups.append(group)
    for query in queries:
        found = vk_api("groups.search", {"q": query, "count": 12, "type": "group", "sort": 0}).get("items", [])
        for group in found:
            group_id = int(group.get("id", 0))
            if group_id and group_id not in seen_groups:
                seen_groups.add(group_id)
                groups.append(group)
        if len(groups) >= 12:
            break
    records = []
    for group in groups[:12]:
        group_id = int(group.get("id", 0))
        if not group_id:
            continue
        slug = group.get("screen_name") or str(group_id)
        try:
            wall = vk_api("wall.get", {"owner_id": -group_id, "count": 20, "filter": "owner"}).get("items", [])
        except (ValueError, OSError, TypeError, json.JSONDecodeError):
            continue
        for post in wall[:20]:
            post_id = int(post.get("id", 0))
            text = clean_markup(post.get("text", ""))[:900]
            if not post_id or not text:
                continue
            url = f"https://vk.com/{slug}?w=wall-{group_id}_{post_id}"
            records.append({"source": "vk", "title": text[:180], "excerpt": text, "url": url,
                            "published": str(post.get("date", ""))[:100], "item_type": "post",
                            "community": clean_markup(group.get("name", ""))[:180], "region": region[:180],
                            "author": clean_markup(group.get("name", ""))[:180],
                            "engagement": int(post.get("comments", {}).get("count", 0)) + int(post.get("likes", {}).get("count", 0))})
            try:
                comments = vk_api("wall.getComments", {"owner_id": -group_id, "post_id": post_id, "count": 20,
                                                           "sort": "desc", "preview_length": 0}).get("items", [])
            except (ValueError, OSError, TypeError, json.JSONDecodeError):
                comments = []
            for comment in comments[:20]:
                comment_text = clean_markup(comment.get("text", ""))[:900]
                if not comment_text:
                    continue
                profile_id = int(comment.get("from_id", 0))
                records.append({"source": "vk", "title": comment_text[:180], "excerpt": comment_text,
                                "url": url + "&reply=" + str(comment.get("id", "")),
                                "published": str(comment.get("date", ""))[:100], "item_type": "comment",
                                "community": clean_markup(group.get("name", ""))[:180], "region": region[:180],
                                "author": f"VK user {profile_id}" if profile_id else "VK user",
                                "engagement": int(comment.get("likes", {}).get("count", 0))})
    if not records:
        for query in queries[:2]:
            try:
                posts = vk_api("wall.search", {"q": query, "count": 100, "owners_only": 0}).get("items", [])
            except (ValueError, OSError, TypeError, json.JSONDecodeError):
                continue
            for post in posts:
                owner_id = int(post.get("owner_id", 0))
                post_id = int(post.get("id", 0))
                text = clean_markup(post.get("text", ""))[:900]
                if owner_id >= 0 or not post_id or not text:
                    continue
                records.append({"source": "vk", "title": text[:180], "excerpt": text,
                                "url": f"https://vk.com/wall{owner_id}_{post_id}",
                                "published": str(post.get("date", ""))[:100], "item_type": "post",
                                "community": "Публичная стена VK", "region": region[:180],
                                "author": "VK community",
                                "engagement": int(post.get("comments", {}).get("count", 0)) + int(post.get("likes", {}).get("count", 0))})
            if records:
                break
    return records


def news_agent(question, country="RU"):
    locale = COUNTRIES.get(country, COUNTRIES["RU"])
    language = locale[1]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": question, "hl": language, "gl": country if country in COUNTRIES else "RU",
        "ceid": f"{country}:{language}" if country in COUNTRIES else "RU:ru"})
    root = ET.fromstring(fetch(url))
    records = []
    for item in root.findall("./channel/item")[:30]:
        link = safe_url(item.findtext("link"))
        title = clean_markup(item.findtext("title"))[:350]
        if link and title:
            records.append({"source": "news", "country": country, "title": title,
                            "excerpt": clean_markup(item.findtext("description"))[:650],
                            "url": link, "published": (item.findtext("pubDate") or "")[:100]})
    return records


def social_agent(question, hashtag):
    if not hashtag:
        return []
    url = "https://mastodon.social/api/v1/timelines/tag/" + urllib.parse.quote(hashtag, safe="") + "?limit=40"
    statuses = json.loads(fetch(url))
    if not isinstance(statuses, list):
        raise ValueError("Неожиданный ответ социальной сети")
    terms = [term for term in re.findall(r"[\wа-яё]{4,}", question.lower()) if term not in STOP]
    records = []
    for status in statuses[:40]:
        if not isinstance(status, dict):
            continue
        text = clean_markup(status.get("content", ""))[:900]
        link = safe_url(status.get("url"))
        if not link or not text or (terms and not any(term in text.lower() for term in terms)):
            continue
        records.append({"source": "social", "title": text[:180], "excerpt": text,
                        "url": link, "published": str(status.get("created_at", ""))[:100]})
    return records


def bluesky_agent(question):
    url = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?" + urllib.parse.urlencode({
        "q": question, "limit": 30, "sort": "latest"})
    data = json.loads(fetch(url))
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        raise ValueError("Неожиданный ответ Bluesky")
    records = []
    for post in data["posts"][:30]:
        if not isinstance(post, dict):
            continue
        author = post.get("author") or {}
        record = post.get("record") or {}
        handle = author.get("handle", "") if isinstance(author, dict) else ""
        uri = post.get("uri", "")
        text = clean_markup(record.get("text", ""))[:900] if isinstance(record, dict) else ""
        if not isinstance(uri, str) or not isinstance(handle, str) or not re.fullmatch(r"[a-zA-Z0-9.-]{3,255}", handle) or not text:
            continue
        rkey = uri.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[a-zA-Z0-9]{5,40}", rkey):
            continue
        records.append({"source": "social", "title": text[:180], "excerpt": text,
                        "url": f"https://bsky.app/profile/{handle}/post/{rkey}",
                        "published": str(record.get("createdAt", ""))[:100]})
    return records


def summarize(rows, countries=()):
    """Describe observed coverage and cite evidence; geographic context is not author nationality."""
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

    selected = [code for code in countries if code in COUNTRIES]
    by_country = []
    for code in selected:
        items = [row for row in rows if row.get("country") == code]
        by_country.append({"code": code, "name": COUNTRIES[code][0], "total": len(items), **overview(items)})
    narrative_counts = Counter(theme["name"] for theme in overview(rows)["themes"])
    narratives = [{"name": name, "count": count, "share": round(count / len(rows) * 100, 1) if rows else 0}
                  for name, count in narrative_counts.most_common()]
    social_text = " ".join(row["title"] + " " + row.get("excerpt", "") for row in rows if row.get("source") in ("social", "vk", "telegram")).lower()
    SOCIAL_GROUPS = {
        "Страхи": ("страш", "боят", "опасн", "угроз", "страдан", "fear", "afraid", "danger", "worry"),
        "Потребности": ("нужн", "требу", "хотим", "нужда", "need", "want", "require", "support"),
        "Недовольство": ("плох", "ужас", "ненавиж", "долг", "коррупц", "bad", "hate", "corrupt", "expensive"),
        "Решения и инициативы": ("предлаг", "решен", "помощ", "инициатив", "вместе", "solution", "help", "initiative"),
    }
    social_analysis = []
    for label, stems in SOCIAL_GROUPS.items():
        hits = sum(1 for row in rows if row.get("source") in ("social", "vk", "telegram") and any(stem in (row["title"] + " " + row.get("excerpt", "")).lower() for stem in stems))
        if hits:
            social_analysis.append({"name": label, "count": hits, "share": round(hits / max(1, sum(row.get("source") in ("social", "vk") for row in rows)) * 100, 1)})
    persons = Counter(row.get("author", "") for row in rows if row.get("author") and row.get("source") == "vk")
    positive = ("хорош", "поддерж", "успех", "помог", "спас", "рад", "справил", "great", "support", "success", "help")
    negative = ("плох", "страш", "опасн", "ненавиж", "проблем", "жалоб", "угроз", "ужас", "bad", "fear", "danger", "problem", "hate")
    sentiment = Counter()
    for row in rows:
        text = (row.get("title", "") + " " + row.get("excerpt", "")).lower()
        pos, neg = sum(text.count(word) for word in positive), sum(text.count(word) for word in negative)
        sentiment["positive" if pos > neg else "negative" if neg > pos else "neutral"] += 1
    return {"total": len(rows), "news": counts["news"], "social": counts["social"], "telegram": counts["telegram"],
            "terms": [{"word": word, "count": count} for word, count in words.most_common(10)],
            "overall": overview(rows), "countries": by_country, "narratives": narratives,
            "social_analysis": social_analysis, "vk_communities": Counter(row.get("community", "") for row in rows if row.get("community") and row.get("source") == "vk").most_common(10),
            "vk_persons": [{"name": name, "count": count} for name, count in persons.most_common(10)],
            "sentiment": {"positive": sentiment["positive"], "neutral": sentiment["neutral"], "negative": sentiment["negative"]},
            "unassigned_social": sum(row["source"] in ("social", "telegram") and not row.get("country") for row in rows),
            "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def collect(db, monitor, fetch_news=news_agent, fetch_social=social_agent, fetch_bluesky=bluesky_agent, fetch_vk=vk_agent, fetch_telegram=telegram_agent):
    """Each collector fails independently; previously saved observations remain available."""
    results = {}
    new_count = 0
    batches = {}
    countries = json.loads(monitor.get("countries") or "[]")
    # Existing monitors retain their former Russian-locale search.
    region = str(monitor.get("region", "")).strip()
    telegram_channels = json.loads(monitor.get("telegram_channels") or "[]")
    if region:
        links = json.loads(monitor.get("vk_group_links") or "[]")
        args = (monitor["question"], region, links) if links else (monitor["question"], region)
        searches = [("vk", fetch_vk, args, "vk")]
    else:
        searches = [("news", fetch_news, (monitor["question"], code), code) for code in (countries or ["RU"])]
    if not region:
        searches.append(("social", fetch_social, (monitor["question"], monitor["hashtag"]), "mastodon"))
        searches.append(("social", fetch_bluesky, (monitor["question"],), "bluesky"))
    if not region and monitor.get("vk_query", monitor["question"]):
        searches.append(("vk", fetch_vk, (monitor.get("vk_query") or monitor["question"],), "vk"))
    if telegram_channels:
        searches.append(("telegram", fetch_telegram, (monitor["question"], region, telegram_channels), "telegram"))
    for name, agent, args, code in searches:
        label = f"news:{code}" if countries and name == "news" else (code if name == "social" else name)
        if code == "mastodon" and not monitor["hashtag"]:
            results[label] = "Не настроен хэштег"
            continue
        try:
            records = agent(*args)
            if not isinstance(records, list):
                raise ValueError("Источник вернул некорректный список")
            batches[label] = (name, "" if name in ("social", "vk") else code, records[:100])
        except Exception as exc:
            results[label] = "Ошибка источника: " + str(exc)[:140]
    for label, (name, code, records) in batches.items():
        try:
            found = 0
            for record in records:
                if not isinstance(record, dict):
                    continue
                link = safe_url(record.get("url"))
                if not link or record.get("source") != name:
                    continue
                cursor = db.execute("""INSERT OR IGNORE INTO media_items
                    (monitor_id, source, country, title, excerpt, url, published, collected_at, item_type, author, community, engagement)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (monitor["id"], name, code,
                    str(record.get("title", ""))[:350], str(record.get("excerpt", ""))[:900],
                    link, str(record.get("published", ""))[:100], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    str(record.get("item_type", "publication"))[:30], str(record.get("author", ""))[:180],
                    str(record.get("community", ""))[:180], max(0, int(record.get("engagement", 0) or 0))))
                found += cursor.rowcount
            new_count += found
            results[label] = f"Добавлено: {found}"
        except (ValueError, OSError, TypeError) as exc:
            results[label] = "Ошибка источника: " + str(exc)[:140]
    if countries:
        results["news"] = "; ".join(f"{COUNTRIES[code][0]}: {results.get('news:' + code, 'нет данных')}" for code in countries)
    social_results = [f"telegram: {results.get('telegram', 'нет данных')}"] if telegram_channels else []
    results["social"] = ("; ".join([f"vk: {results.get('vk', 'нет данных')}", *social_results]) if region else
                         "; ".join([*(f"{source}: {results.get(source, 'нет данных')}" for source in ("mastodon", "bluesky", "vk")), *social_results]))
    return new_count, results
