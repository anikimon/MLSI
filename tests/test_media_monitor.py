import sqlite3
import tempfile
import unittest

from media_monitor import collect, summarize


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


if __name__ == "__main__":
    unittest.main()
