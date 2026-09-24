import unittest

from media_monitor import summarize


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


if __name__ == "__main__":
    unittest.main()
