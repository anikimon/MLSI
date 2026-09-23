"""Reproducible, anonymous sample survey for exercising reporting features."""

import json
import random
from datetime import datetime, timezone


DEMO_TITLE = "ДЕМО · Warhammer 40,000: интерес к настольному хобби"
DEMO_COUNT = 385

QUESTIONS = [
    {"id": "q1", "label": "Как вы знакомы с Warhammer 40,000?", "type": "single", "required": True,
     "options": ["Играю регулярно", "Играю иногда", "Знаком(а) с историей вселенной", "Пока мало знаком(а)"]},
    {"id": "q2", "label": "Какой формат вам наиболее интересен?", "type": "single", "required": True,
     "options": ["Настольные игры", "Покраска миниатюр", "Книги и сюжеты", "Видеоигры"]},
    {"id": "q3", "label": "Какая фракция вам интереснее всего?", "type": "single", "required": True,
     "options": ["Космодесант", "Орки", "Некроны", "Эльдары", "Тираниды", "Пока не выбрал(а)"]},
    {"id": "q4", "label": "Какие занятия в хобби вам нравятся?", "type": "multiple", "required": False,
     "options": ["Играть", "Красить", "Читать", "Обсуждать лор", "Собирать модели"]},
    {"id": "q5", "label": "Сколько лет вы знакомы с этой вселенной?", "type": "number", "required": False, "options": []},
    {"id": "q6", "label": "Где вы чаще узнаёте новости о хобби?", "type": "single", "required": True,
     "options": ["Сообщества", "Друзья", "Видео", "Магазины и клубы"]},
    {"id": "q7", "label": "Что сильнее всего мешает заниматься хобби?", "type": "single", "required": True,
     "options": ["Стоимость", "Мало времени", "Нет компании", "Ничего не мешает"]},
    {"id": "q8", "label": "Планируете ли покупать миниатюры в ближайший год?", "type": "single", "required": True,
     "options": ["Да", "Возможно", "Нет"]},
    {"id": "q9", "label": "Насколько вероятно, что вы посоветуете хобби другу? (0–10)",
     "type": "number", "required": True, "options": []},
    {"id": "q10", "label": "Что привлекает вас в этой вселенной?", "type": "text", "required": False, "options": []},
]


def demo_responses():
    """Use a fixed seed so every installation starts with the same fictional data."""
    rng = random.Random(40000)
    for _ in range(DEMO_COUNT):
        familiarity = rng.choices(QUESTIONS[0]["options"], weights=[27, 31, 26, 16])[0]
        activity = rng.choices(QUESTIONS[1]["options"], weights=[31, 22, 20, 27])[0]
        faction = rng.choices(QUESTIONS[2]["options"], weights=[28, 14, 17, 12, 12, 17])[0]
        interests = [option for option, chance in zip(QUESTIONS[3]["options"], [.44, .37, .51, .56, .41])
                     if rng.random() < chance]
        years = "" if familiarity == "Пока мало знаком(а)" else str(rng.randint(1, 17))
        purchase = rng.choices(QUESTIONS[7]["options"],
                               weights=[44, 40, 16] if familiarity.startswith("Играю") else [17, 46, 37])[0]
        score = min(10, max(0, round(rng.gauss(8 if familiarity.startswith("Играю") else 6, 2))))
        answers = {
            "q1": familiarity, "q2": activity, "q3": faction, "q4": interests, "q5": years,
            "q6": rng.choices(QUESTIONS[5]["options"], weights=[36, 20, 31, 13])[0],
            "q7": rng.choices(QUESTIONS[6]["options"], weights=[43, 29, 18, 10])[0],
            "q8": purchase, "q9": str(score),
            "q10": rng.choice(["", "Сюжет", "Миниатюры", "Совместные игры", "Покраска"]),
        }
        yield ("", json.dumps(answers, ensure_ascii=False))


def ensure_demo_study(db):
    """Insert the sample exactly once, after an administrator has been created."""
    if db.execute("SELECT 1 FROM demo_study LIMIT 1").fetchone():
        return
    owner = db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    if not owner:
        return
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    study = db.execute("""INSERT INTO studies (title, description, goal, tasks, stage, responsible_id, created_at)
        VALUES (?, ?, ?, ?, 'processing', ?, ?)""", (
            DEMO_TITLE,
            "Учебный проект. Все 385 анкет синтетические: они не отражают мнения реальных людей.",
            "Проверить работу анкеты, статистического отчёта, аналитической записки и презентации на вымышленных данных.",
            "Описать интерес к форматам хобби\nСравнить предпочтения по вопросам анкеты\nПроверить ограничения интерпретации",
            owner["id"], timestamp))
    study_id = study.lastrowid
    db.execute("INSERT INTO questionnaires (study_id, questions, updated_at) VALUES (?, ?, ?)",
               (study_id, json.dumps(QUESTIONS, ensure_ascii=False), timestamp))
    db.executemany("""INSERT INTO responses (study_id, interviewer_id, code, answers, created_at, weight, source)
        VALUES (?, ?, ?, ?, ?, 1, 'excel')""", (
            (study_id, owner["id"], code, answers, timestamp) for code, answers in demo_responses()))
    db.execute("INSERT INTO demo_study (id, study_id) VALUES (1, ?)", (study_id,))
