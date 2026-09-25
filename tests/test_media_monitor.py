import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from media_monitor import collect, summarize, vk_agent


class SummaryTest(unittest.TestCase):
    def test_rising_terms_are_observed_with_country_context(self):
        titles = ["Economy and markets", "Economy and trade", "Markets improve",
                  "Climate flooding report", "Climate flooding warning", "Climate flooding response"]
        rows = [{"id": index, "source": "news", "country": "US", "title": title,
                 "excerpt": "", "url": f"https://example.org/{index}"}
                for index, title in enumerate(titles, 1)]
        summary = summarize(rows, ["US", "FR"])
        self.assertEqual([country["total"] for country in summary["countries"]], [6, 0])
        self.assertIn("climate", [term["word"] for term in summary["countries"][0]["trending_terms"]])
        self.assertEqual(summary["countries"][1]["description"], "Публикаций пока нет.")

    def test_direction_only_from_explicit_phrase(self):
        rows = [{"id": 1, "source": "news", "country": "US", "title": "Russia attacks Ukraine",
                 "excerpt": "", "url": "https://example.org/1"},
                {"id": 2, "source": "news", "country": "US", "title": "US warns Russia after attack",
                 "excerpt": "", "url": "https://example.org/2"}]
        items = summarize(rows, ["US"])["countries"][0]["conflict_mentions"]
        self.assertEqual(items[0]["direction"], {"actor_mentioned": "Россия", "target_mentioned": "Украина"})
        self.assertIsNone(items[1]["direction"])

    def test_vk_region_collection_and_sentiment(self):
        calls = []
        def fake_vk(question, region):
            calls.append((question, region))
            return [{"source": "vk", "title": "В регионе нужна помощь, но есть хороший результат",
                     "excerpt": "Публичное обсуждение события", "url": "https://vk.com/public/1?w=wall-1_1",
                     "published": "2026-01-01", "item_type": "comment", "community": "Новости региона",
                     "author": "VK user 1", "engagement": 4}]

        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(f"{directory}/media.db")
            db.execute("""CREATE TABLE media_items (
                id INTEGER PRIMARY KEY, monitor_id INTEGER, source TEXT, country TEXT, title TEXT, excerpt TEXT,
                url TEXT UNIQUE, published TEXT, collected_at TEXT, item_type TEXT, author TEXT, community TEXT, engagement INTEGER)""")
            monitor = {"id": 1, "question": "события", "region": "ЛНР", "countries": "[]", "hashtag": ""}
            added, result = collect(db, monitor, fetch_vk=fake_vk)
            db.commit()
            rows = [dict(zip(("id", "source", "country", "title", "excerpt", "url", "published", "item_type", "author", "community", "engagement"), row))
                    for row in db.execute("SELECT id, source, country, title, excerpt, url, published, item_type, author, community, engagement FROM media_items")]
            db.close()
        self.assertEqual(calls, [("события", "ЛНР")])
        self.assertEqual(added, 1)
        self.assertEqual(result["social"], "vk: Добавлено: 1")
        self.assertEqual(summarize(rows)["sentiment"]["positive"], 1)

    def test_vk_search_uses_region_first_and_keeps_posts_when_comments_fail(self):
        calls = []

        def fake_api(method, params):
            calls.append((method, params))
            if method == "groups.search":
                return {"items": [{"id": 7, "screen_name": "region_news", "name": "Новости региона"}]}
            if method == "wall.get":
                return {"items": [{"id": 11, "text": "В регионе обсуждают важное событие",
                                    "date": 1, "comments": {"count": 2}, "likes": {"count": 3}}]}
            raise ValueError("комментарии закрыты")

        with patch("media_monitor.vk_api", side_effect=fake_api):
            records = vk_agent("события", "Луганская Народная Республика")

        self.assertEqual(records[0]["item_type"], "post")
        self.assertEqual(records[0]["community"], "Новости региона")
        self.assertEqual(calls[0][0], "groups.search")
        self.assertEqual(calls[0][1]["q"], "Луганская Народная Республика")

    def test_vk_wall_search_is_used_when_communities_have_no_posts(self):
        def fake_api(method, params):
            if method == "groups.search":
                return {"items": []}
            if method == "wall.search":
                return {"items": [{"owner_id": -9, "id": 4, "text": "Обсуждение региона",
                                    "date": 1, "comments": {"count": 1}, "likes": {"count": 2}}]}
            raise AssertionError(method)

        with patch("media_monitor.vk_api", side_effect=fake_api):
            records = vk_agent("события", "ЛНР")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["url"], "https://vk.com/wall-9_4")

    def test_vk_manual_group_links_are_prioritized(self):
        calls = []

        def fake_api(method, params):
            calls.append((method, params))
            if method == "groups.search":
                return {"items": []}
            if method == "wall.get":
                return {"items": [{"id": 3, "text": "Пост из указанной группы", "date": 1,
                                    "comments": {"count": 0}, "likes": {"count": 0}}]}
            if method == "wall.getComments":
                return {"items": []}
            raise AssertionError(method)

        with patch("media_monitor.vk_api", side_effect=fake_api):
            records = vk_agent("события", "регион", ["https://vk.com/club123"])

        self.assertEqual(len(records), 1)
        self.assertEqual(calls[0][0], "groups.search")
        wall_call = next(call for call in calls if call[0] == "wall.get")
        self.assertEqual(wall_call[1]["owner_id"], -123)

    def test_vk_manual_group_links_accept_vk_ru_and_prefixed_slugs(self):
        calls = []

        def fake_api(method, params):
            calls.append((method, params))
            if method == "groups.search":
                return {"items": []}
            if method == "groups.getById":
                return {"groups": [{"id": 456, "screen_name": "example", "name": "Example"}]}
            if method == "wall.get":
                return {"items": []}
            if method == "wall.search":
                return {"items": []}
            raise AssertionError(method)

        with patch("media_monitor.vk_api", side_effect=fake_api):
            vk_agent("события", "регион", ["https://vk.ru/public/example?from=groups"])

        self.assertIn(("groups.getById", {"group_ids": "example"}), calls)

    def test_telegram_collection_is_saved_as_social_source(self):
        calls = []

        def fake_telegram(question, region, channels):
            calls.append((question, region, channels))
            return [{"source": "telegram", "title": "Обсуждение события",
                     "excerpt": "Публичный пост канала", "url": "https://t.me/example/42",
                     "published": "2026-01-01", "item_type": "post", "community": "Канал",
                     "author": "Канал", "engagement": 7}]

        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(f"{directory}/media.db")
            db.execute("""CREATE TABLE media_items (
                id INTEGER PRIMARY KEY, monitor_id INTEGER, source TEXT, country TEXT, title TEXT, excerpt TEXT,
                url TEXT UNIQUE, published TEXT, collected_at TEXT, item_type TEXT, author TEXT, community TEXT, engagement INTEGER)""")
            monitor = {"id": 1, "question": "события", "region": "ЛНР", "countries": "[]", "hashtag": "",
                       "telegram_channels": "[\"https://t.me/example\"]"}
            added, result = collect(db, monitor, fetch_vk=lambda question, region: [], fetch_telegram=fake_telegram)
            db.commit()
            row = db.execute("SELECT source, url, community FROM media_items").fetchone()
            db.close()
        self.assertEqual(calls, [("события", "ЛНР", ["https://t.me/example"])])
        self.assertEqual(added, 1)
        self.assertEqual(result["social"], "vk: Добавлено: 0; telegram: Добавлено: 1")
        self.assertEqual(row, ("telegram", "https://t.me/example/42", "Канал"))


if __name__ == "__main__":
    unittest.main()
