import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rubric

BASE_DIR = Path(__file__).resolve().parent
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
VISION_API_BASE = os.environ.get("VISION_API_BASE", "").rstrip("/")
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-4o-mini")

MAT_ROOTS = [
    "хуй", "хуя", "хуе", "пизд", "бля", "блят", "ебан", "ебал", "ебат", "ебуч",
    "еби", "ебу", "ёб", "мудак", "мудил", "сука", "сучк", "гондон", "долбоёб",
    "долбоеб", "залуп", "мразь", "уёб", "уеб", "нах", "похер", "срать", "жоп",
]


def default_key():
    path = BASE_DIR / "key.txt"
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


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


def deepseek_json(system, user, key, timeout=90, temperature=0.15):
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
    return parse_json_block(payload["choices"][0]["message"]["content"])


def clamp(value, low=0.0, high=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n+", text or "") if s.strip()]


def find_evidence(text, keywords, limit=3):
    found = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            found.append(sentence[:300])
            if len(found) >= limit:
                break
    return found


def detect_mat(text):
    lowered = (text or "").lower()
    hits = [root for root in MAT_ROOTS if root in lowered]
    return hits


def foreign_ratio(text):
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", text or "")
    if not words:
        return 0.0
    latin = sum(1 for word in words if re.fullmatch(r"[a-zA-Z]+", word))
    return round(latin / len(words), 3)


class SectionAgent:
    def __init__(self, section, key=""):
        self.section = section
        self.key = key

    def run(self, text, title=""):
        if self.key:
            try:
                return self.llm_run(text, title)
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        return self.heuristic(text)

    def prompt(self, text, title):
        lines = []
        for pid, name, description, _ in self.section["provisions"]:
            lines.append(f'{pid}: {name} — {description}')
        provision_list = "\n".join(lines)
        system = (
            "Ты эксперт по анализу медиатекстов на соответствие Основам государственной политики "
            "по сохранению и укреплению традиционных российских духовно-нравственных ценностей "
            "(Указ Президента РФ № 809). Ты даёшь аналитическую подсказку, а не юридическое "
            "заключение. Учитывай контекст, иронию и то, утверждает материал ценность, отрицает "
            "её или просто упоминает. Для каждого пункта верни строго JSON-объект с ключом "
            '"results": массив из {"id", "status", "confidence", "evidence", "comment"}. '
            f'status — одно из: "{rubric.STATUS_COMPLIANT}", "{rubric.STATUS_PARTIAL}", '
            f'"{rubric.STATUS_VIOLATION}", "{rubric.STATUS_NA}". confidence — 0..1. '
            "evidence — массив точных цитат из текста (до 3, каждая до 300 символов). "
            "comment — краткое пояснение на русском."
        )
        user = (
            f"Раздел рубрики: {self.section['title']} ({self.section['code']}).\n"
            f"{self.section['instruction']}\n\n"
            f"Пункты:\n{provision_list}\n\n"
            f"Заголовок материала: {title}\n\nТекст материала:\n{text[:12000]}\n\n"
            "Верни JSON с оценкой всех пунктов."
        )
        return system, user

    def llm_run(self, text, title):
        system, user = self.prompt(text, title)
        data = deepseek_json(system, user, self.key)
        items = {item.get("id"): item for item in data.get("results", []) if isinstance(item, dict)}
        results = []
        for pid, name, description, _ in self.section["provisions"]:
            item = items.get(pid, {})
            status = item.get("status")
            if status not in rubric.STATUS_LABELS:
                status = rubric.STATUS_NA
            evidence = item.get("evidence")
            if not isinstance(evidence, list):
                evidence = []
            results.append({
                "id": pid,
                "title": name,
                "description": description,
                "status": status,
                "confidence": clamp(item.get("confidence", 0.5)),
                "evidence": [str(quote)[:300] for quote in evidence[:3]],
                "comment": str(item.get("comment", ""))[:600],
            })
        return results

    def heuristic(self, text):
        results = []
        for pid, name, description, keywords in self.section["provisions"]:
            evidence = find_evidence(text, keywords)
            kind = self.section["kind"]
            if evidence:
                if kind == "value":
                    status = rubric.STATUS_COMPLIANT
                    comment = "Найдены маркеры ценности. Эвристика без анализа контекста."
                else:
                    status = rubric.STATUS_VIOLATION
                    comment = "Найдены маркеры риска. Требуется проверка контекста человеком."
                confidence = 0.35
            else:
                status = rubric.STATUS_NA
                comment = "Маркеры не найдены (локальный режим)."
                confidence = 0.3
            results.append({
                "id": pid,
                "title": name,
                "description": description,
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "comment": comment,
            })
        return results


class LanguageAgent(SectionAgent):
    def heuristic(self, text):
        results = []
        mat_hits = detect_mat(text)
        ratio = foreign_ratio(text)
        for pid, name, description, keywords in self.section["provisions"]:
            if pid == "l1":
                status = rubric.STATUS_VIOLATION if mat_hits else rubric.STATUS_COMPLIANT
                evidence = find_evidence(text, mat_hits) if mat_hits else []
                comment = (
                    f"Обнаружена ненормативная лексика: {', '.join(mat_hits[:5])}."
                    if mat_hits else "Нецензурной лексики не обнаружено."
                )
                confidence = 0.6 if mat_hits else 0.5
            elif pid == "l2":
                status = rubric.STATUS_PARTIAL if ratio > 0.3 else rubric.STATUS_COMPLIANT
                evidence = []
                comment = f"Доля латиницы среди слов: {round(ratio * 100, 1)}%."
                confidence = 0.4
            else:
                evidence = find_evidence(text, keywords)
                status = rubric.STATUS_VIOLATION if evidence else rubric.STATUS_NA
                comment = "Возможные признаки искажения истории — нужна проверка." if evidence else "Не применимо."
                confidence = 0.3
            results.append({
                "id": pid, "title": name, "description": description, "status": status,
                "confidence": confidence, "evidence": evidence, "comment": comment,
            })
        return results


class VisionAgent:
    name = "vision"

    def __init__(self, key="", api_base="", model=""):
        self.api_key = api_key = key or VISION_API_KEY
        self.api_base = (api_base or VISION_API_BASE).rstrip("/")
        self.model = model or VISION_MODEL

    @property
    def available(self):
        return bool(self.api_key and self.api_base)

    def run(self, frames):
        if not self.available:
            return {"available": False, "note": "Визуальный анализ не настроен (VISION_API_KEY/VISION_API_BASE).", "findings": []}
        findings = []
        import base64

        for index, frame in enumerate(frames[:6]):
            image = base64.b64encode(frame).decode("ascii")
            payload = json.dumps({
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Опиши сцену кадра и укажи, есть ли признаки тем, "
                                                 "противоречащих традиционным российским духовно-нравственным "
                                                 "ценностям (насилие, безнравственность, пропаганда). Кратко."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
                    ],
                }],
                "temperature": 0.2,
            }).encode("utf-8")
            request = urllib.request.Request(
                f"{self.api_base}/chat/completions", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                findings.append({"frame": index + 1, "note": data["choices"][0]["message"]["content"][:600]})
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as exc:
                findings.append({"frame": index + 1, "note": f"Кадр не проанализирован: {exc}"})
        return {"available": True, "model": self.model, "findings": findings}


def build_index(results):
    counts = {status: 0 for status in rubric.STATUS_LABELS}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    applicable = sum(count for status, count in counts.items() if status != rubric.STATUS_NA)
    compliant = counts[rubric.STATUS_COMPLIANT]
    partial = counts[rubric.STATUS_PARTIAL]
    violation = counts[rubric.STATUS_VIOLATION]
    index = round((compliant + 0.5 * partial) / applicable * 100) if applicable else None
    return {
        "counts": counts,
        "applicable": applicable,
        "compliant": compliant,
        "partial": partial,
        "violation": violation,
        "compliance_index": index,
    }


class ComplianceNetwork:
    def __init__(self, key=""):
        self.key = key

    def analyze(self, text, title="", frames=None):
        engines = set()
        results = []

        def run_section(section):
            agent = LanguageAgent(section, self.key) if section["id"] == "language" else SectionAgent(section, self.key)
            return section, agent.run(text, title)

        if self.key:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(run_section, section) for section in rubric.SECTIONS]
                for future in as_completed(futures):
                    section, section_results = future.result()
                    for item in section_results:
                        item["section"] = section["id"]
                        item["section_title"] = section["title"]
                        item["code"] = f"{section['code']} · {item['id']}"
                    results.extend(section_results)
            engines.add("deepseek")
        else:
            for section in rubric.SECTIONS:
                _, section_results = run_section(section)
                for item in section_results:
                    item["section"] = section["id"]
                    item["section_title"] = section["title"]
                    item["code"] = f"{section['code']} · {item['id']}"
                results.extend(section_results)
            engines.add("local")

        order = {pid: index for index, pid in enumerate(provision["id"] for provision in rubric.all_provisions())}
        results.sort(key=lambda item: order.get(item["id"], 999))

        index = build_index(results)
        visual = None
        if frames:
            visual = VisionAgent(self.key).run(frames)
            engines.add("vision" if visual.get("available") else "vision-off")

        summary = SummaryAgent(self.key).summarize(index, results, title)
        return {
            "results": results,
            "index": index,
            "summary": summary,
            "visual": visual,
            "model": DEEPSEEK_MODEL if self.key else "local",
            "engines": sorted(engines),
            "decree": rubric.DECREE,
        }


class SummaryAgent:
    def __init__(self, key=""):
        self.key = key

    def summarize(self, index, results, title):
        if self.key:
            system = (
                "Ты аналитик, готовящий справку о соответствии материала Основам государственной "
                "политики по традиционным ценностям (Указ № 809). Дай структурированный отчёт на "
                "русском: что соответствует, что вызывает вопросы, что не соответствует, с опорой "
                "на пункты рубрики. Отдели факты от интерпретаций, укажи ограничения и что проверить "
                "человеку. Не выноси юридический вердикт. Верни JSON: {\"summary\": \"...\"}."
            )
            compact = [
                {"code": item["code"], "title": item["title"], "status": item["status"],
                 "confidence": item["confidence"], "evidence": item["evidence"], "comment": item["comment"]}
                for item in results
            ]
            user = (
                f"Материал: {title}\nИндекс соответствия: {index['compliance_index']}%\n"
                f"Сводка: {json.dumps(index['counts'], ensure_ascii=False)}\n\n"
                f"Оценки по пунктам:\n{json.dumps(compact, ensure_ascii=False, indent=1)[:10000]}"
            )
            try:
                data = deepseek_json(system, user, self.key)
                if isinstance(data.get("summary"), str) and data["summary"].strip():
                    return data["summary"]
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        return local_summary(index, results, title)


def local_summary(index, results, title):
    lines = [f"Аналитическая справка (локальный режим) по материалу: {title or 'без названия'}."]
    if index["compliance_index"] is not None:
        lines.append(f"Индекс соответствия: {index['compliance_index']}% (по {index['applicable']} применимым пунктам).")
    else:
        lines.append("Применимых пунктов не выявлено.")
    lines.append(
        f"Соответствует: {index['compliant']}, частично: {index['partial']}, "
        f"не соответствует: {index['violation']}, не применимо: {index['counts'][rubric.STATUS_NA]}."
    )
    violations = [item for item in results if item["status"] == rubric.STATUS_VIOLATION]
    partial = [item for item in results if item["status"] == rubric.STATUS_PARTIAL]
    if violations:
        lines.append("\nВызывают вопросы (не соответствует):")
        for item in violations:
            quote = f' — «{item["evidence"][0]}»' if item["evidence"] else ""
            lines.append(f"  • {item['code']} {item['title']}: {item['comment']}{quote}")
    if partial:
        lines.append("\nЧастичные замечания:")
        for item in partial:
            lines.append(f"  • {item['code']} {item['title']}: {item['comment']}")
    if not violations and not partial:
        lines.append("\nЯвных несоответствий локальные правила не обнаружили.")
    lines.append(
        "\nЭто аналитическая подсказка, а не юридическое заключение. Подключите ключ DeepSeek для "
        "содержательного разбора и проверьте выводы вручную."
    )
    return "\n".join(lines)
