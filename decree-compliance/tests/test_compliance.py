import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["DEEPSEEK_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agents  # noqa: E402
import rubric  # noqa: E402
import server  # noqa: E402


class RubricTest(unittest.TestCase):
    def test_provisions_present(self):
        provisions = rubric.all_provisions()
        self.assertGreaterEqual(len(provisions), 30)
        ids = [item["id"] for item in provisions]
        self.assertEqual(ids.count("v1"), 1)
        self.assertIn("d7", ids)
        self.assertIn("r_h", ids)
        self.assertEqual(len(set(ids)), len(ids))

    def test_digest(self):
        digest = rubric.rubric_digest()
        self.assertIn("809", digest)
        self.assertIn("Традиционные ценности", digest)


class AgentTest(unittest.TestCase):
    def test_index_calculation(self):
        results = [
            {"status": rubric.STATUS_COMPLIANT},
            {"status": rubric.STATUS_COMPLIANT},
            {"status": rubric.STATUS_PARTIAL},
            {"status": rubric.STATUS_VIOLATION},
            {"status": rubric.STATUS_NA},
        ]
        index = agents.build_index(results)
        self.assertEqual(index["applicable"], 4)
        self.assertEqual(index["compliance_index"], round((2 + 0.5) / 4 * 100))

    def test_local_network(self):
        network = agents.ComplianceNetwork("")
        result = network.analyze(
            "Наша семья и дети — главная ценность. Ветераны хранят историческую память.", "Тест"
        )
        self.assertEqual(result["model"], "local")
        self.assertGreater(len(result["results"]), 30)
        statuses = {item["status"] for item in result["results"]}
        self.assertTrue(statuses.issubset(set(rubric.STATUS_LABELS)))
        family = next(item for item in result["results"] if item["id"] == "v8")
        self.assertEqual(family["status"], rubric.STATUS_COMPLIANT)

    def test_mat_detection(self):
        self.assertTrue(agents.detect_mat("вот блин, какой хуй"))
        self.assertFalse(agents.detect_mat("спокойный информационный текст"))

    def test_local_summary(self):
        network = agents.ComplianceNetwork("")
        result = network.analyze("Пропаганда наркотиков и насилия.", "Плохой текст")
        self.assertTrue(result["summary"])
        self.assertIn("не юридическое заключение", result["summary"].lower().replace("ё", "е"))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = server.create_server("127.0.0.1", 0, str(Path(self.tmp.name) / "c.db"))
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
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def test_health_and_rubric(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(data["ai_enabled"])
        status, data = self.request("GET", "/api/rubric")
        self.assertEqual(status, 200)
        self.assertEqual(data["decree"]["number"], "809")
        self.assertGreaterEqual(len(data["sections"]), 4)

    def test_text_material_flow(self):
        status, data = self.request("POST", "/api/materials", {
            "title": "Статья о семье",
            "text": "Крепкая семья, дети и уважение к старшим — основа общества. Ветераны хранят историческую память.",
        })
        self.assertEqual(status, 201)
        material_id = data["id"]

        status, data = self.request("POST", f"/api/materials/{material_id}/analyze")
        self.assertEqual(status, 200)
        self.assertEqual(data["report"]["model"], "local")
        self.assertIsInstance(data["report"]["index"]["compliance_index"], int)

        status, data = self.request("GET", f"/api/materials/{material_id}/report")
        self.assertEqual(status, 200)
        self.assertGreater(len(data["report"]["results"]), 30)

        status, data = self.request("GET", "/api/materials")
        self.assertEqual(len(data["materials"]), 1)
        self.assertTrue(data["materials"][0]["analyzed"])

        status, _ = self.request("DELETE", f"/api/materials/{material_id}")
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/materials")
        self.assertEqual(len(data["materials"]), 0)

    def test_report_requires_analysis(self):
        status, data = self.request("POST", "/api/materials", {"title": "X", "text": "текст"})
        material_id = data["id"]
        status, _ = self.request("GET", f"/api/materials/{material_id}/report")
        self.assertEqual(status, 404)

    def test_empty_upload_rejected(self):
        status, _ = self.request("POST", "/api/materials", {"title": "Пусто", "text": ""})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
