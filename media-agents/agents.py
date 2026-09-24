import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DEFAULT_FEEDS = [
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "language": "en"},
    {"name": "CNN World", "url": "http://rss.cnn.com/rss/edition_world.rss", "language": "en"},
    {"name": "Reuters World", "url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best", "language": "en"},
    {"name": "Deutsche Welle", "url": "https://rss.dw.com/rdf/rss-en-world", "language": "en"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "language": "en"},
    {"name": "The Guardian World", "url": "https://www.theguardian.com/world/rss", "language": "en"},
    {"name": "Le Monde", "url": "https://www.lemonde.fr/international/rss_full.xml", "language": "fr"},
    {"name": "El Pais", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada", "language": "es"},
]

POSITIVE = {
    "agreement", "peace", "success", "growth", "aid", "support", "recovery", "improve",
    "win", "victory", "hope", "cooperation", "breakthrough", "agreement", "safe",
    "celebrate", "progress", "benefit", "rebuild", "truce", "ceasefire",
    "мир", "соглашение", "успех", "рост", "помощь", "поддержка", "развитие", "победа",
    "надежда", "сотрудничество", "прорыв", "безопасность", "прогресс", "перемирие",
}
NEGATIVE = {
    "war", "attack", "crisis", "collapse", "threat", "conflict", "kill", "dead",
    "death", "sanction", "protest", "recession", "disaster", "corruption", "decline",
    "fear", "violence", "crisis", "victim", "destroy", "fail", "loss", "tension",
    "война", "атака", "кризис", "крах", "угроза", "конфликт", "убить", "смерть",
    "санкции", "протест", "рецессия", "катастрофа", "коррупция", "страх", "насилие",
    "жертва", "разрушение", "провал", "потери", "напряжённость",
}
AGGRESSION_TERMS = {
    "war": 3, "invasion": 3, "strike": 2, "missile": 3, "bomb": 3, "drone": 2,
    "attack": 2, "offensive": 2, "destroy": 3, "kill": 3, "eliminate": 3,
    "retaliate": 3, "escalation": 2, "mobilization": 2, "hostile": 2, "threat": 2,
    "sanctions": 1, "confrontation": 2, "weapons": 2, "troops": 1, "front line": 3,
    "война": 3, "вторжение": 3, "удар": 2, "ракета": 3, "бомба": 3, "дрон": 2,
    "атака": 2, "наступление": 2, "уничтожить": 3, "убить": 3, "ликвидировать": 3,
    "ответный": 3, "эскалация": 2, "мобилизация": 2, "враждебный": 2, "угроза": 2,
    "санкции": 1, "конфронтация": 2, "оружие": 2, "войска": 1, "линия фронта": 3,
}
COUNTRIES = [
    "Russia", "Ukraine", "United States", "USA", "America", "China", "NATO",
    "European Union", "EU", "Israel", "Iran", "Palestine", "Gaza", "Germany",
    "France", "United Kingdom", "Britain", "Turkey", "Syria", "India", "Japan",
    "North Korea", "South Korea", "Taiwan", "Belarus", "Poland",
    "Россия", "Украина", "США", "Америка", "Китай", "НАТО", "Евросоюз",
    "Израиль", "Иран", "Палестина", "Газа", "Германия", "Франция",
    "Великобритания", "Британия", "Турция", "Сирия", "Индия", "Япония",
    "Северная Корея", "Южная Корея", "Тайвань", "Беларусь", "Польша",
]
COUNTRIES_CANON = {
    "usa": "США", "america": "США", "united states": "США",
    "uk": "Великобритания", "britain": "Великобритания", "united kingdom": "Великобритания",
    "eu": "Евросоюз", "european union": "Евросоюз",
    "nato": "НАТО",
}
CANON = {
    "россия": "Россия", "russia": "Россия",
    "украина": "Украина", "ukraine": "Украина",
    "сша": "США", "usa": "США", "america": "США", "united states": "США",
    "китай": "Китай", "china": "Китай",
    "нато": "НАТО", "nato": "НАТО",
    "евросоюз": "Евросоюз", "eu": "Евросоюз", "european union": "Евросоюз",
    "израиль": "Израиль", "israel": "Израиль",
    "иран": "Иран", "iran": "Иран",
    "палестина": "Палестина", "palestine": "Палестина",
    "газа": "Газа", "gaza": "Газа",
    "германия": "Германия", "germany": "Германия",
    "франция": "Франция", "france": "Франция",
    "великобритания": "Великобритания", "britain": "Великобритания",
    "турция": "Турция", "turkey": "Турция",
    "сирия": "Сирия", "syria": "Сирия",
    "индия": "Индия", "india": "Индия",
    "япония": "Япония", "japan": "Япония",
    "северная корея": "Северная Корея", "north korea": "Северная Корея",
    "тайвань": "Тайвань", "taiwan": "Тайвань",
}


def default_key():
    path = BASE_DIR / "key.txt"
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def fingerprint(source, title, link):
    raw = f"{(source or '').strip().lower()}|{(title or '').strip().lower()}|{(link or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for element in root.iter():
        if local_name(element.tag) not in ("item", "entry"):
            continue
        record = {"title": "", "summary": "", "link": "", "published": ""}
        for child in element:
            name = local_name(child.tag)
            if name == "title":
                record["title"] = strip_html(child.text or "")
            elif name in ("description", "summary", "encoded", "content"):
                if not record["summary"]:
                    record["summary"] = strip_html(child.text or "")
            elif name == "link":
                if isinstance(child.text, str) and child.text.strip():
                    record["link"] = child.text.strip()
                elif child.attrib.get("href"):
                    record["link"] = child.attrib["href"].strip()
            elif name in ("pubdate", "published", "updated", "date"):
                record["published"] = child.text or ""
        if record["title"]:
            items.append(record)
    return items


def fetch_feed(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "MediaAgents/1.0 (+research)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return parse_feed(raw)


def tokenize(text):
    return re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", (text or "").lower())


def heuristic_tonality(text):
    words = tokenize(text)
    if not words:
        return "neutral", 0.0, "Пустой текст"
    positive = sum(1 for word in words if word in POSITIVE)
    negative = sum(1 for word in words if word in NEGATIVE)
    total = positive + negative
    if total == 0:
        return "neutral", 0.0, "Оценочных слов не найдено"
    score = (positive - negative) / total
    if score > 0.15:
        return "positive", round(score, 3), f"Позитивных слов {positive}, негативных {negative}"
    if score < -0.15:
        return "negative", round(score, 3), f"Негативных слов {negative}, позитивных {positive}"
    return "neutral", round(score, 3), f"Смешанная лексика ({positive}+ / {negative}-)"


def heuristic_aggression(text):
    lowered = (text or "").lower()
    words = tokenize(lowered)
    score = 0
    hits = []
    for term, weight in AGGRESSION_TERMS.items():
        if " " in term:
            count = lowered.count(term)
        else:
            count = words.count(term)
        if count:
            score += weight * min(count, 3)
            hits.append(term)
    exclamations = lowered.count("!")
    if exclamations:
        score += min(exclamations, 3)
    letters = [c for c in (text or "") if c.isalpha()]
    if letters:
        uppercase = sum(1 for c in letters if c.isupper()) / len(letters)
        if uppercase > 0.3:
            score += 2
    level = max(0, min(10, round(score / 2)))
    reason = "Найдены маркеры: " + ", ".join(hits[:8]) if hits else "Агрессивных маркеров нет"
    return level, reason


def heuristic_target(text):
    counts = {}
    for name in COUNTRIES:
        pattern = r"\b" + re.escape(name) + r"\b"
        found = len(re.findall(pattern, text or "", flags=re.IGNORECASE))
        if found:
            canon = CANON.get(name.lower(), name)
            counts[canon] = counts.get(canon, 0) + found
    if not counts:
        return "не определён", "unknown", "Упоминаний известных акторов нет"
    target = max(counts, key=counts.get)
    reason = "Частые упоминания: " + ", ".join(f"{k} ({v})" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:4])
    return target, "country", reason


def heuristic_translate(title, text):
    return title or "", text or ""


def parse_json_block(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("В ответе модели нет JSON")
    return json.loads(text[start : end + 1])


def deepseek_json(system, user, key, timeout=60, temperature=0.2):
    body = json.dumps(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    return parse_json_block(content)


def clamp(value, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


class Agent:
    name = "agent"
    label = "Агент"

    def __init__(self, key=""):
        self.key = key

    def run(self, article):
        raise NotImplementedError

    def llm(self, system, user):
        return deepseek_json(system, user, self.key)


class TranslatorAgent(Agent):
    name = "translator"
    label = "Переводчик"

    def run(self, article):
        fallback = {"title": article["title"], "text": article["text"]}
        if not self.key:
            return fallback
        system = (
            "Ты переводчик новостей. Переведи заголовок и текст на русский язык, "
            "сохраняя факты и нейтральность. Верни строго JSON: "
            '{"title": "...", "text": "..."}.'
        )
        user = f"Заголовок: {article['title']}\n\nТекст: {article['text'][:4000]}"
        try:
            data = self.llm(system, user)
            return {
                "title": str(data.get("title") or article["title"]),
                "text": str(data.get("text") or article["text"]),
            }
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            return fallback


class TonalityAgent(Agent):
    name = "tonality"
    label = "Тональность"

    def run(self, article):
        text = article["translated_text"] or article["text"]
        if self.key:
            system = (
                "Ты аналитик тональности новостей. Оцени общую тональность материала "
                "по отношению к описываемым событиям. Верни строго JSON: "
                '{"tonality": "negative|neutral|positive", "score": -1..1, "reason": "..."}.'
            )
            try:
                data = self.llm(system, f"Заголовок: {article['title']}\n\n{text[:4000]}")
                tonality = str(data.get("tonality", "neutral")).lower()
                if tonality not in ("negative", "neutral", "positive"):
                    tonality = "neutral"
                return {
                    "tonality": tonality,
                    "tonality_score": clamp(data.get("score", 0), -1, 1),
                    "tonality_reason": str(data.get("reason", ""))[:1000],
                    "engine": "deepseek",
                }
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        tonality, score, reason = heuristic_tonality(text)
        return {"tonality": tonality, "tonality_score": score, "tonality_reason": reason, "engine": "local"}


class AggressionAgent(Agent):
    name = "aggression"
    label = "Уровень агрессии"

    def run(self, article):
        text = article["translated_text"] or article["text"]
        if self.key:
            system = (
                "Ты эксперт по медиа-агрессии. Оцени уровень агрессии текста по шкале "
                "0–10, где 0 — спокойная информация, 10 — прямая враждебность, угрозы "
                "или призывы к насилию. Верни строго JSON: "
                '{"level": 0..10, "score": 0..10, "reason": "..."}.'
            )
            try:
                data = self.llm(system, f"Заголовок: {article['title']}\n\n{text[:4000]}")
                return {
                    "aggression_level": clamp(data.get("level", data.get("score", 0)), 0, 10),
                    "aggression_reason": str(data.get("reason", ""))[:1000],
                    "engine": "deepseek",
                }
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        level, reason = heuristic_aggression(text)
        return {"aggression_level": level, "aggression_reason": reason, "engine": "local"}


class TargetAgent(Agent):
    name = "target"
    label = "Объект агрессии"

    def run(self, article):
        text = article["translated_text"] or article["text"]
        if self.key:
            system = (
                "Определи объект агрессии или критики — против кого (чего) направлен "
                "негативный или агрессивный посыл новости. Верни строго JSON: "
                '{"target": "...", "target_type": "country|organization|person|group|unknown", '
                '"reason": "..."}. Если агрессии нет, укажи target "не определён".'
            )
            try:
                data = self.llm(system, f"Заголовок: {article['title']}\n\n{text[:4000]}")
                target_type = str(data.get("target_type", "unknown")).lower()
                if target_type not in ("country", "organization", "person", "group", "unknown"):
                    target_type = "unknown"
                return {
                    "target": str(data.get("target") or "не определён")[:200],
                    "target_type": target_type,
                    "target_reason": str(data.get("reason", ""))[:1000],
                    "engine": "deepseek",
                }
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        target, target_type, reason = heuristic_target(text)
        return {"target": target, "target_type": target_type, "target_reason": reason, "engine": "local"}


ANALYSIS_AGENTS = (TranslatorAgent, TonalityAgent, AggressionAgent, TargetAgent)


class Network:
    def __init__(self, key="", max_workers=4):
        self.key = key
        self.max_workers = max_workers

    def analyze(self, article):
        translated = {"title": article["title"], "text": article["text"]}
        result = {
            "translated_title": article["title"],
            "translated_text": article["text"],
            "tonality": "neutral",
            "tonality_score": 0.0,
            "tonality_reason": "",
            "aggression_level": 0,
            "aggression_reason": "",
            "target": "не определён",
            "target_type": "unknown",
            "target_reason": "",
            "engines": [],
            "model": DEEPSEEK_MODEL if self.key else "local",
        }

        translator = TranslatorAgent(self.key)
        try:
            translated = translator.run(article)
        except Exception:
            translated = {"title": article["title"], "text": article["text"]}
        result["translated_title"] = translated["title"]
        result["translated_text"] = translated["text"]

        enriched = dict(article)
        enriched["translated_text"] = translated["text"]

        agents = [TonalityAgent(self.key), AggressionAgent(self.key), TargetAgent(self.key)]
        engines = set()
        if self.key:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(agent.run, enriched): agent for agent in agents}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                    except Exception:
                        continue
                    result.update(data)
                    engines.add(data.get("engine", "local"))
        else:
            for agent in agents:
                data = agent.run(enriched)
                result.update(data)
                engines.add(data.get("engine", "local"))
        result["engines"] = sorted(engines) or ["local"]
        return result


class SummaryAgent(Agent):
    name = "summary"
    label = "Аналитик"

    def summarize(self, stats, articles):
        if self.key:
            system = (
                "Ты руководитель аналитического центра, изучающего зарубежные СМИ. "
                "На основе агрегированных показателей сделай выводы на русском: общий "
                "уровень агрессии и тональность, ключевые объекты агрессии, наиболее "
                "агрессивные источники и материалы, возможные тенденции. Отделяй факты "
                "от интерпретаций и укажи ограничения. Объём — 4–8 абзацев."
            )
            user = json.dumps(stats, ensure_ascii=False, indent=2)
            sample = "\n".join(
                f"- [{a['source']}] {a['title']} (агрессия {a['aggression_level']}/10, "
                f"тональность {a['tonality']}, объект: {a['target']})"
                for a in articles[:40]
            )
            try:
                data = self.llm(system + "\n\nМатериалы:\n" + sample, user)
                if isinstance(data.get("content"), str):
                    return data["content"]
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        return local_summary(stats, articles)


def local_summary(stats, articles):
    if not stats.get("total"):
        return "Нет проанализированных материалов для выводов."
    lines = [
        f"Проанализировано материалов: {stats['total']}.",
        f"Средний уровень агрессии: {stats['avg_aggression']}/10.",
        f"Средняя тональность: {stats['avg_tonality']} (от -1 до 1).",
        f"Доля негативных материалов: {stats['negative_share']}%.",
        f"Доля материалов с агрессией 7+: {stats['high_aggression_share']}%.",
    ]
    if stats.get("targets"):
        top = ", ".join(f"{name} ({count})" for name, count in stats["targets"][:5])
        lines.append(f"Основные объекты агрессии: {top}.")
    if stats.get("sources"):
        top = ", ".join(f"{name}: {count}" for name, count in stats["sources"][:5])
        lines.append(f"Источники: {top}.")
    if stats.get("most_aggressive"):
        lines.append("Наиболее агрессивные материалы:")
        for item in stats["most_aggressive"][:5]:
            lines.append(f"  • [{item['source']}] {item['title']} — {item['level']}/10")
    lines.append(
        "Выводы сделаны локальными правилами (без ИИ). Подключите ключ DeepSeek для "
        "содержательной интерпретации. Оценки не заменяют редакционную экспертизу."
    )
    return "\n".join(lines)


def build_stats(records):
    total = len(records)
    stats = {
        "total": total,
        "avg_aggression": 0.0,
        "avg_tonality": 0.0,
        "negative_share": 0.0,
        "high_aggression_share": 0.0,
        "tonality_counts": {"negative": 0, "neutral": 0, "positive": 0},
        "targets": [],
        "sources": [],
        "most_aggressive": [],
    }
    if not records:
        return stats
    aggression_values = [float(r["aggression_level"]) for r in records]
    tonality_values = [float(r["tonality_score"]) for r in records]
    stats["avg_aggression"] = round(sum(aggression_values) / total, 2)
    stats["avg_tonality"] = round(sum(tonality_values) / total, 2)
    negative = sum(1 for r in records if r["tonality"] == "negative")
    high = sum(1 for r in records if float(r["aggression_level"]) >= 7)
    stats["negative_share"] = round(negative / total * 100, 1)
    stats["high_aggression_share"] = round(high / total * 100, 1)
    for record in records:
        tonality = record["tonality"]
        if tonality in stats["tonality_counts"]:
            stats["tonality_counts"][tonality] += 1
    target_counts = {}
    source_counts = {}
    for record in records:
        target = (record.get("target") or "").strip()
        if target and target != "не определён":
            target_counts[target] = target_counts.get(target, 0) + 1
        source = (record.get("source") or "").strip() or "Неизвестно"
        source_counts[source] = source_counts.get(source, 0) + 1
    stats["targets"] = sorted(target_counts.items(), key=lambda x: -x[1])
    stats["sources"] = sorted(source_counts.items(), key=lambda x: -x[1])
    stats["most_aggressive"] = [
        {"title": r["title"], "source": r.get("source", ""), "level": r["aggression_level"]}
        for r in sorted(records, key=lambda r: -float(r["aggression_level"]))[:5]
    ]
    return stats
