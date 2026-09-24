import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

os.environ["DEEPSEEK_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agents as ml  # noqa: E402
import server  # noqa: E402


class HeuristicTest(unittest.TestCase):
    def test_tonality(self):
        label, score, _ = ml.heuristic_tonality("Leaders celebrate a peace agreement and economic growth")
        self.assertEqual(label, "positive")
        self.assertGreater(score, 0)
        label, score, _ = ml.heuristic_tonality("A deadly war and a brutal attack killed civilians")
        self.assertEqual(label, "negative")
        self.assertLess(score, 0)

    def test_aggression(self):
        level, _ = ml.heuristic_aggression("Massive missile strike and invasion destroy cities, troops escalate the war")
        self.assertGreaterEqual(level, 6)
        level, _ = ml.heuristic_aggression("The parliament discussed the annual budget on Tuesday")
        self.assertLessEqual(level, 2)

    def test_target(self):
        target, kind, _ = ml.heuristic_target("NATO and the United States condemned Russia after the attack")
        self.assertEqual(kind, "country")
        self.assertIn(target, ("США", "НАТО", "Россия"))
        target, kind, _ = ml.heuristic_target("Local weather will be mild today")
        self.assertEqual(kind, "unknown")

    def test_network_local(self):
        network = ml.Network("")
        result = network.analyze({
            "title": "War escalates", "text": "Missiles strike the city as troops advance", "source": "Test",
        })
        self.assertEqual(result["model"], "local")
        self.assertIn("local", result["engines"])
        self.assertIn(result["tonality"], ("negative", "neutral", "positive"))
        self.assertGreaterEqual(result["aggression_level"], 0)

    def test_build_stats(self):
        records = [
            {"source": "A", "title": "t1", "tonality": "negative", "tonality_score": -0.5, "aggression_level": 8, "target": "Россия"},
            {"source": "A", "title": "t2", "tonality": "neutral", "tonality_score": 0.0, "aggression_level": 2, "target": "США"},
            {"source": "B", "title": "t3", "tonality": "negative", "tonality_score": -0.2, "aggression_level": 9, "target": "Россия"},
        ]
        stats = ml.build_stats(records)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["negative_share"], 66.7)
        self.assertEqual(stats["high_aggression_share"], 66.7)
        self.assertEqual(stats["targets"][0], ("Россия", 2))
        self.assertEqual(stats["most_aggressive"][0]["title"], "t3")


class FeedParserTest(unittest.TestCase):
    def test_parse_rss(self):
        raw = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>War in region</title>
            <description>&lt;p&gt;Missile strike reported&lt;/p&gt;</description>
            <link>https://example.com/a</link>
            <pubDate>Tue, 01 Apr 2025 10:00:00 GMT</pubDate></item>
          <item><title>Peace talks resume</title>
            <description>Officials meet</description>
            <link>https://example.com/b</link></item>
        </channel></rss>"""
        items = ml.parse_feed(raw)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "War in region")
        self.assertEqual(items[0]["summary"], "Missile strike reported")
        self.assertEqual(items[0]["link"], "https://example.com/a")

    def test_parse_atom(self):
        raw = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>Summit held</title><summary>Leaders agree</summary>
            <link href="https://example.com/c"/><updated>2025-04-01T10:00:00Z</updated></entry>
        </feed>"""
        items = ml.parse_feed(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://example.com/c")


class ServerTest(unittest.TestCase):
    def setUp(self):
        local_key = patch.object(ml, "default_key", return_value="")
        local_key.start()
        self.addCleanup(local_key.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = server.create_server("127.0.0.1", 0, str(Path(self.tmp.name) / "media.db"))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {}, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def test_health_and_seeded_feeds(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(data["ai_enabled"])
        status, data = self.request("GET", "/api/feeds")
        self.assertGreater(len(data["feeds"]), 0)

    def test_manual_analyze_and_report(self):
        status, data = self.request("POST", "/api/articles", {
            "title": "Missile attack escalates war",
            "source": "Тест",
            "text": "Russian troops launched a missile strike, the United States condemned the attack.",
        })
        self.assertEqual(status, 201)
        article_id = data["id"]

        status, data = self.request("GET", "/api/articles?only=unanalyzed")
        self.assertEqual(len(data["articles"]), 1)

        status, data = self.request("POST", f"/api/articles/{article_id}/analyze")
        self.assertEqual(status, 200)
        self.assertEqual(data["analysis"]["model"], "local")
        self.assertGreater(data["analysis"]["aggression_level"], 0)

        status, data = self.request("GET", "/api/stats")
        self.assertEqual(data["stats"]["analyzed_articles"], 1)
        self.assertEqual(data["stats"]["total_articles"], 1)

        status, data = self.request("POST", "/api/reports")
        self.assertEqual(status, 201)
        self.assertTrue(data["report"]["content"])

        status, data = self.request("GET", "/api/reports")
        self.assertEqual(len(data["reports"]), 1)

    def test_batch_analyze(self):
        for index in range(3):
            self.request("POST", "/api/articles", {"title": f"News {index}", "text": "peace agreement"})
        status, data = self.request("POST", "/api/analyze", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["processed"], 3)
        status, data = self.request("GET", "/api/articles?only=unanalyzed")
        self.assertEqual(len(data["articles"]), 0)

    def test_feed_validation(self):
        status, _ = self.request("POST", "/api/feeds", {"name": "Bad", "url": "not-a-url"})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/feeds", {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"})
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
