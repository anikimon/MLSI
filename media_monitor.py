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
AGENTS = ("СМИ · Google News RSS", "Соцсети · Mastodon", "Соцсети · Bluesky", "Аналитик · сводка")
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
    return {"total": len(rows), "news": counts["news"], "social": counts["social"],
            "terms": [{"word": word, "count": count} for word, count in words.most_common(10)],
            "overall": overview(rows), "countries": by_country,
            "unassigned_social": sum(row["source"] == "social" and not row.get("country") for row in rows),
            "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def collect(db, monitor, fetch_news=news_agent, fetch_social=social_agent, fetch_bluesky=bluesky_agent):
    """Each collector fails independently; previously saved observations remain available."""
    results = {}
    new_count = 0
    batches = {}
    countries = json.loads(monitor.get("countries") or "[]")
    # Existing monitors retain their former Russian-locale search.
    searches = [("news", fetch_news, (monitor["question"], code), code) for code in (countries or ["RU"])]
    searches.append(("social", fetch_social, (monitor["question"], monitor["hashtag"]), "mastodon"))
    searches.append(("social", fetch_bluesky, (monitor["question"],), "bluesky"))
    for name, agent, args, code in searches:
        label = f"news:{code}" if countries and name == "news" else (code if name == "social" else name)
        if code == "mastodon" and not monitor["hashtag"]:
            results[label] = "Не настроен хэштег"
            continue
        try:
            records = agent(*args)
            if not isinstance(records, list):
                raise ValueError("Источник вернул некорректный список")
            batches[label] = (name, "" if name == "social" else code, records[:30])
        except (ValueError, OSError, ET.ParseError, TypeError, json.JSONDecodeError) as exc:
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
                    (monitor_id, source, country, title, excerpt, url, published, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (monitor["id"], name, code,
                    str(record.get("title", ""))[:350], str(record.get("excerpt", ""))[:900],
                    link, str(record.get("published", ""))[:100], datetime.now(timezone.utc).isoformat(timespec="seconds")))
                found += cursor.rowcount
            new_count += found
            results[label] = f"Добавлено: {found}"
        except (ValueError, OSError, TypeError) as exc:
            results[label] = "Ошибка источника: " + str(exc)[:140]
    if countries:
        results["news"] = "; ".join(f"{COUNTRIES[code][0]}: {results.get('news:' + code, 'нет данных')}" for code in countries)
    results["social"] = "; ".join(f"{source}: {results.get(source, 'нет данных')}" for source in ("mastodon", "bluesky"))
    return new_count, results
