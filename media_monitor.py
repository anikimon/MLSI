"""Bounded public-source collectors and deterministic media analysis."""

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone


MAX_RESPONSE = 1024 * 1024
AGENTS = ("СМИ · Google News RSS", "Соцсети · Mastodon", "Аналитик · сводка")
STOP = {"это", "как", "что", "для", "или", "при", "про", "the", "and", "with", "from", "сми", "новости"}


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


def news_agent(question):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": question, "hl": "ru", "gl": "RU", "ceid": "RU:ru"})
    root = ET.fromstring(fetch(url))
    records = []
    for item in root.findall("./channel/item")[:30]:
        link = safe_url(item.findtext("link"))
        title = clean_markup(item.findtext("title"))[:350]
        if link and title:
            records.append({"source": "news", "title": title,
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


def summarize(rows):
    """Summarize observed publications without inventing sentiment or facts."""
    counts = Counter(row["source"] for row in rows)
    words = Counter(word for row in rows for word in set(re.findall(r"[\wа-яё]{4,}", row["title"].lower()))
                    if word not in STOP and not word.isdigit())
    return {"total": len(rows), "news": counts["news"], "social": counts["social"],
            "terms": [{"word": word, "count": count} for word, count in words.most_common(10)],
            "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def collect(db, monitor, fetch_news=news_agent, fetch_social=social_agent):
    """Each collector fails independently; previously saved observations remain available."""
    results = {}
    new_count = 0
    batches = {}
    for name, agent, args in (("news", fetch_news, (monitor["question"],)),
                              ("social", fetch_social, (monitor["question"], monitor["hashtag"]))):
        if name == "social" and not monitor["hashtag"]:
            results[name] = "Не настроен хэштег"
            continue
        try:
            records = agent(*args)
            if not isinstance(records, list):
                raise ValueError("Источник вернул некорректный список")
            batches[name] = records[:40]
        except (ValueError, OSError, ET.ParseError, TypeError) as exc:
            results[name] = "Ошибка источника: " + str(exc)[:140]
    for name, records in batches.items():
        try:
            found = 0
            for record in records:
                if not isinstance(record, dict):
                    continue
                link = safe_url(record.get("url"))
                if not link or record.get("source") != name:
                    continue
                cursor = db.execute("""INSERT OR IGNORE INTO media_items
                    (monitor_id, source, title, excerpt, url, published, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""", (monitor["id"], name,
                    str(record.get("title", ""))[:350], str(record.get("excerpt", ""))[:900],
                    link, str(record.get("published", ""))[:100], datetime.now(timezone.utc).isoformat(timespec="seconds")))
                found += cursor.rowcount
            new_count += found
            results[name] = f"Добавлено: {found}"
        except (ValueError, OSError, TypeError) as exc:
            results[name] = "Ошибка источника: " + str(exc)[:140]
    return new_count, results
