# -*- coding: utf-8 -*-
"""Генератор вшитых документов для игры «Проректор».

Скрипт собирает 400 документов (280 служебных документов и 120 презентаций)
на основе комбинаций отправителей, видов, направлений и соответствия
Указу Президента РФ от 09.11.2022 № 809.

Запуск:
    python build_docs.py

Результат:
    docs.js    — window.RECTOR_DOCS = {...};
    docs.json  — тот же список для отладки и тестов.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SEED = 8092022
N_DOCS = 280
N_PRESENTATIONS = 120
TOTAL = N_DOCS + N_PRESENTATIONS

DECREE = {
    "title": "Указ Президента Российской Федерации от 09.11.2022 № 809",
    "url": "http://www.kremlin.ru/acts/bank/48502",
}

# Направления работы: ключ, название, тема писем, подчинённое подразделение,
# профильный проректор и повод для презентаций.
AREAS = [
    {
        "key": "edu",
        "label": "Учебная работа",
        "topics": [
            "развитии учебного процесса",
            "обновлении образовательных программ",
            "наставничестве первокурсников",
            "проведении предметных олимпиад",
            "итоговой аттестации",
            "языковой подготовке студентов",
        ],
        "ptopics": [
            "Приёмная кампания 2026",
            "Программа наставничества",
            "Пилотные образовательные программы",
            "Языковая школа университета",
            "Олимпиада для школьников",
            "Итоги учебного года",
        ],
        "dept": {"key": "upravlenie-ucheb", "name": "Учебно-методическое управление"},
        "pr": {"key": "prorector-ucheb", "name": "Проректор по учебной работе"},
    },
    {
        "key": "sci",
        "label": "Наука и исследования",
        "topics": [
            "развитии научных исследований",
            "поддержке молодых учёных",
            "проведении научных конференций",
            "грантовой программе",
            "научной этике и публикациях",
            "работе диссертационных советов",
        ],
        "ptopics": [
            "Новая лаборатория искусственного интеллекта",
            "Грантовый проект кафедры",
            "Итоги научного года",
            "Совет молодых учёных",
            "Научная конференция «Россия и мир»",
            "Центр коллективного пользования",
        ],
        "dept": {"key": "upravlenie-nauka", "name": "Управление науки"},
        "pr": {"key": "prorector-nauka", "name": "Проректор по научной работе"},
    },
    {
        "key": "youth",
        "label": "Молодёжная политика и воспитание",
        "topics": [
            "воспитательной работе со студентами",
            "развитии студенческого самоуправления",
            "патриотических мероприятиях",
            "волонтёрском движении",
            "работе студенческих отрядов",
            "профилактике асоциальных явлений",
        ],
        "ptopics": [
            "Студенческий фестиваль «Весна»",
            "Волонтёрский центр",
            "Историко-патриотический клуб",
            "Студенческое самоуправление",
            "День знаний",
            "Экологическая акция кампуса",
        ],
        "dept": {"key": "upravlenie-molodezh", "name": "Управление молодёжной политики"},
        "pr": {"key": "prorector-molodezh", "name": "Проректор по молодёжной политике"},
    },
    {
        "key": "intl",
        "label": "Международная деятельность",
        "topics": [
            "развитии международного сотрудничества",
            "обучении иностранных студентов",
            "экспорте образования",
            "совместных программах с зарубежными вузами",
            "участии в международных рейтингах",
            "летних школах для иностранцев",
        ],
        "ptopics": [
            "Экспорт образования",
            "Летняя школа для иностранных студентов",
            "Партнёрство с зарубежными вузами",
            "Международный рейтинг",
            "Русский язык как иностранный",
            "Академическая мобильность",
        ],
        "dept": {"key": "upravlenie-mezhdunarod", "name": "Управление международных связей"},
        "pr": {"key": "prorector-mezhdunarod", "name": "Проректор по международной деятельности"},
    },
    {
        "key": "press",
        "label": "Информационная политика и СМИ",
        "topics": [
            "информационном освещении деятельности вуза",
            "работе с публикациями в СМИ",
            "ведении официальных сообществ",
            "подготовке пресс-релизов",
            "противодействии недостоверной информации",
            "медиаграмотности студентов",
        ],
        "ptopics": [
            "Медиацентр университета",
            "Официальные сообщества вуза",
            "Пресс-портрет университета",
            "Медиаграмотность студентов",
            "Кампания в социальных сетях",
            "Годовой медиаотчёт",
        ],
        "dept": {"key": "press-sluzhba", "name": "Пресс-служба"},
        "pr": {"key": "prorector-kommunikacii", "name": "Проректор по коммуникациям"},
    },
    {
        "key": "legal",
        "label": "Правовое и административное обеспечение",
        "topics": [
            "соблюдении законодательства",
            "подготовке локальных нормативных актов",
            "закупочной деятельности",
            "договорной работе",
            "защите персональных данных",
            "административно-хозяйственных вопросах",
        ],
        "ptopics": [
            "Регламент закупок",
            "Локальные нормативные акты",
            "Защита персональных данных",
            "Договорная работа вуза",
            "Хозяйственный план кампуса",
            "Правовой ликбез для сотрудников",
        ],
        "dept": {"key": "yuridicheskiy", "name": "Юридический отдел"},
        "pr": {"key": "prorector-pravovoy", "name": "Проректор по правовым вопросам"},
    },
]

EXTERNAL_SENDERS = [
    "Минобрнауки России",
    "Минпросвещения России",
    "Минкультуры России",
    "Минспорт России",
    "Росмолодёжь",
    "Российское общество «Знание»",
    "Совет ректоров вузов России",
    "Департамент образования города",
    "Федеральное агентство по делам молодёжи",
    "Общественная палата Российской Федерации",
    "Аппарат полномочного представителя Президента",
    "Российский союз ректоров",
    "Иностранный университет-партнёр",
    "Международная ассоциация университетов",
    "Благотворительный фонд «Родина»",
    "Госкорпорация «Росатом»",
    "ПАО «Газпром»",
    "АНО «Цифровая экономика»",
    "Русская православная церковь (епархия)",
    "Редакция журнала «Вести образования»",
]

INTERNAL_SENDERS = [
    "Студенческий совет",
    "Профком студентов",
    "Кафедра философии",
    "Кафедра информатики",
    "Лаборатория социологии",
    "Дирекция института",
    "Совет молодых учёных",
    "Студенческое научное общество",
    "Волонтёрский центр",
    "Редакция студенческой газеты",
    "Отдел аспирантуры",
    "Библиотека университета",
    "Спортивный клуб",
    "Медиацентр",
    "Кафедра иностранных языков",
]

EXTERNAL_KINDS = [
    "Письмо",
    "Обращение",
    "Ходатайство",
    "Информационное письмо",
    "Проект приказа",
    "Проект соглашения",
    "Проект договора",
    "Методические рекомендации",
]

INTERNAL_KINDS = [
    "Служебная записка",
    "Заявка",
    "Отчёт",
    "Докладная записка",
    "Предложение",
    "План мероприятий",
    "Проект сметы",
    "Программа",
]

PRESENTATION_KINDS = [
    "Презентация",
    "Слайды",
    "Презентация проекта",
    "Афиша",
    "Демонстрационные материалы",
    "Презентация к совету",
]

TRADITIONAL_VALUES = [
    "жизнь",
    "достоинство",
    "права и свободы человека",
    "патриотизм",
    "гражданственность",
    "служение Отечеству",
    "высокие нравственные идеалы",
    "крепкая семья",
    "созидательный труд",
    "гуманизм",
    "милосердие",
    "справедливость",
    "коллективизм",
    "взаимопомощь",
    "взаимоуважение",
    "историческая память",
    "преемственность поколений",
    "единство народов России",
]

COMPLIANT_SENTENCES = [
    "Особое внимание уделено сохранению и укреплению таких традиционных ценностей, как {values}.",
    "В основе инициативы лежат традиционные российские ценности: {values}.",
    "Материалы направлены на укрепление таких ценностей, как {values}, и на противодействие деструктивной идеологии.",
    "Документ опирается на Основы государственной политики (Указ № 809) и развивает ценности: {values}.",
    "Проект формирует у обучающихся уважение к таким ценностям, как {values}.",
    "Мероприятия способствуют сохранению {values} и укреплению единства народов России.",
]

DESTRUCTIVE_SIGNS = [
    "культивирование эгоизма и вседозволенности",
    "отрицание идеалов патриотизма и служения Отечеству",
    "разрушение традиционной семьи",
    "пропаганда нетрадиционных сексуальных отношений",
    "навязывание антиобщественных стереотипов поведения",
    "пропаганда алкоголя и наркотиков",
    "искажение исторической правды",
    "дискредитация воинской и государственной службы",
    "обесценивание созидательного труда",
    "разжигание межнациональной и межрелигиозной розни",
    "отрицание человеческого достоинства и ценности жизни",
    "подрыв доверия к институтам государства",
]

DESTRUCTIVE_SENTENCES = [
    "В документе продвигается {sign}, что противоречит Основам государственной политики (п. 14, 17 Указа № 809).",
    "Материал содержит признаки деструктивной идеологии: {sign}.",
    "Инициатива, по сути, навязывает {sign}.",
    "Проект ориентирован на {sign}, что представляет угрозу традиционным ценностям.",
    "Под благовидным предлогом в материалах проводится {sign}.",
    "Авторы настаивают на {sign} как на норме, что недопустимо.",
]

ASKS_COMPLIANT = [
    "Просим рассмотреть и поддержать инициативу.",
    "Просим учесть при планировании работы.",
    "Направляем для использования в работе.",
    "Просим оказать содействие в реализации.",
]

ASKS_DESTRUCTIVE = [
    "Просим согласовать и выделить финансирование.",
    "Просим поддержать и включить в план работы.",
    "Просим утвердить в предложенной редакции.",
    "Настаиваем на скорейшем согласовании.",
]

PRESENTATION_BULLETS = [
    "Цель и задачи",
    "Ожидаемые результаты",
    "Целевая аудитория",
    "Команда проекта",
    "Календарный план",
    "Необходимые ресурсы",
    "Бюджет и источники финансирования",
    "Показатели эффективности",
    "Партнёры и участники",
    "Риски и способы их снижения",
    "Этапы реализации",
    "Ключевые выводы",
]

PRESENTATION_STRENGTHS = [
    "опора на традиционные российские духовно-нравственные ценности",
    "воспитание гражданственности и патриотизма",
    "сохранение исторической памяти",
    "укрепление крепкой семьи и преемственности поколений",
    "развитие созидательного труда и наставничества",
    "межнациональное и межрелигиозное согласие",
]


def _values_sentence(rnd: random.Random) -> str:
    count = rnd.choice([2, 3])
    values = rnd.sample(TRADITIONAL_VALUES, count)
    joined = " и ".join([", ".join(values[:-1]), values[-1]]) if count > 2 else " и ".join(values)
    return rnd.choice(COMPLIANT_SENTENCES).format(values=joined)


def _sentence_capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def make_document(rnd: random.Random, area: dict, sender: str, sender_type: str,
                  kind: str, stance: str) -> dict:
    topic = rnd.choice(area["topics"])
    level = "coord" if sender_type == "external" else "exec"
    target = area["pr"] if level == "coord" else area["dept"]
    title = f"{kind} о {topic}"

    if stance == "compliant":
        sentence = _values_sentence(rnd)
        ask = rnd.choice(ASKS_COMPLIANT)
        body = f"{sender} направляет материалы о {topic}. {sentence} {ask}"
        decree_ref = "п. 5"
        explanation = (
            "Материал укрепляет традиционные российские духовно-нравственные ценности "
            "(п. 5 Указа № 809). Вердикт — принять и направить исполнителю."
        )
        compliant = True
    else:
        sign = rnd.choice(DESTRUCTIVE_SIGNS)
        ask = rnd.choice(ASKS_DESTRUCTIVE)
        sentence = rnd.choice(DESTRUCTIVE_SENTENCES).format(sign=sign)
        body = f"{sender} направляет материалы о {topic}. {_sentence_capitalize(sentence)} {ask}"
        decree_ref = "п. 14, 17"
        explanation = (
            f"Документ распространяет деструктивную идеологию (п. 14, 17 Указа № 809): {sign}. "
            "Вердикт — отклонить."
        )
        compliant = False

    return {
        "type": "document",
        "kind": kind,
        "title": title,
        "sender": sender,
        "senderType": sender_type,
        "theme": area["key"],
        "themeLabel": area["label"],
        "level": level,
        "levelLabel": "координация" if level == "coord" else "исполнение",
        "destKey": target["key"],
        "destName": target["name"],
        "compliant": compliant,
        "body": body,
        "slides": [],
        "decreeRef": decree_ref,
        "explanation": explanation,
    }


def make_presentation(rnd: random.Random, area: dict, sender: str, sender_type: str,
                      kind: str, topic: str) -> dict:
    level = "coord" if sender_type == "external" else "exec"
    target = area["pr"] if level == "coord" else area["dept"]
    title = f"{kind}: {topic}"
    bullets = [topic]
    bullets.extend(rnd.sample(PRESENTATION_BULLETS, rnd.choice([3, 4])))
    bullets.append(f"Ценность: {rnd.choice(PRESENTATION_STRENGTHS)}")
    return {
        "type": "presentation",
        "kind": kind,
        "title": title,
        "sender": sender,
        "senderType": sender_type,
        "theme": area["key"],
        "themeLabel": area["label"],
        "level": level,
        "levelLabel": "координация" if level == "coord" else "исполнение",
        "destKey": target["key"],
        "destName": target["name"],
        "compliant": None,
        "body": "",
        "slides": bullets,
        "decreeRef": "",
        "explanation": "",
    }


def build_documents() -> list[dict]:
    rnd = random.Random(SEED)
    documents: list[dict] = []
    for area in AREAS:
        for sender_type in ("external", "internal"):
            senders = EXTERNAL_SENDERS if sender_type == "external" else INTERNAL_SENDERS
            kinds = EXTERNAL_KINDS if sender_type == "external" else INTERNAL_KINDS
            for sender in senders:
                for kind in kinds:
                    for stance in ("compliant", "destructive"):
                        documents.append(make_document(rnd, area, sender, sender_type, kind, stance))
    rnd.shuffle(documents)
    return documents[:N_DOCS]


def build_presentations() -> list[dict]:
    rnd = random.Random(SEED + 1)
    presentations: list[dict] = []
    for area in AREAS:
        for sender_type in ("external", "internal"):
            senders = EXTERNAL_SENDERS if sender_type == "external" else INTERNAL_SENDERS
            for sender in senders:
                for kind in PRESENTATION_KINDS:
                    for topic in area["ptopics"]:
                        presentations.append(make_presentation(rnd, area, sender, sender_type, kind, topic))
    rnd.shuffle(presentations)
    return presentations[:N_PRESENTATIONS]


def build_deck() -> list[dict]:
    rnd = random.Random(SEED + 2)
    deck = build_documents() + build_presentations()
    rnd.shuffle(deck)
    for index, doc in enumerate(deck, start=1):
        doc["id"] = index
        doc["case"] = f"2026/{index:04d}"
    return deck


def write_dist(deck: list[dict]) -> None:
    payload = {"decree": DECREE, "documents": deck}
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (BASE_DIR / "docs.json").write_text(compact + "\n", encoding="utf-8")
    js = "window.RECTOR_DOCS = " + compact + ";\n"
    (BASE_DIR / "docs.js").write_text(js, encoding="utf-8")


def main() -> None:
    deck = build_deck()
    write_dist(deck)
    documents = sum(1 for doc in deck if doc["type"] == "document")
    presentations = len(deck) - documents
    print(f"Готово: {len(deck)} документов ({documents} служебных, {presentations} презентаций)")


if __name__ == "__main__":
    main()
