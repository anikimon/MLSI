import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["ALLOW_DEV_CODES"] = "1"
os.environ["SMTP_HOST"] = ""
os.environ["CODE_TTL_SECONDS"] = "600"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def register_admin(self, email="admin@example.com"):
        return self.request(
            "POST", "/api/register", {"name": "Админ", "email": email, "password": "secret123"}
        )

    def create_user(self, name, email, role):
        return self.request(
            "POST", "/api/users", {"name": name, "email": email, "password": "secret123", "role": role}
        )


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = server.create_server("127.0.0.1", 0, str(Path(self.tmp.name) / "test.db"))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.client = Client(self.base)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_setup_and_register(self):
        status, data = self.client.request("GET", "/api/setup")
        self.assertEqual(status, 200)
        self.assertTrue(data["needs_admin"])

        status, _ = self.client.register_admin()
        self.assertEqual(status, 201)

        status, data = self.client.request("GET", "/api/session")
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["role"], "admin")

        status, data = self.client.request("GET", "/api/setup")
        self.assertFalse(data["needs_admin"])

        status, data = self.client.register_admin("second@example.com")
        self.assertEqual(status, 403)

    def test_survey_lifecycle_and_analytics(self):
        self.client.register_admin()
        status, data = self.client.request("POST", "/api/surveys", {"title": "Тест", "goal": "Проверка"})
        self.assertEqual(status, 201)
        survey_id = data["id"]

        questions = [
            {"label": "Возраст", "type": "scale", "required": True, "scale_min": 18, "scale_max": 70, "scale_step": 1},
            {"label": "Пол", "type": "single", "required": True, "options": ["М", "Ж"]},
            {"label": "Интересы", "type": "multiple", "required": False, "options": ["Спорт", "Кино", "Наука"]},
            {"label": "Комментарий", "type": "open", "required": False},
        ]
        status, _ = self.client.request("PUT", f"/api/surveys/{survey_id}/questions", {"questions": questions})
        self.assertEqual(status, 200)

        status, _ = self.client.request(
            "PUT", f"/api/surveys/{survey_id}/questions",
            {"questions": [{"label": "Один", "type": "single", "options": ["A"]}]},
        )
        self.assertEqual(status, 400)

        status, data = self.client.request("GET", f"/api/surveys/{survey_id}")
        self.assertEqual(len(data["survey"]["questions"]), 4)
        qids = [q["id"] for q in data["survey"]["questions"]]

        status, _ = self.client.request(
            "POST", f"/api/surveys/{survey_id}/responses",
            {"answers": {str(qids[0]): 30, str(qids[1]): "М", str(qids[2]): ["Спорт", "Наука"], str(qids[3]): "норм"}},
        )
        self.assertEqual(status, 201)

        status, _ = self.client.request(
            "POST", f"/api/surveys/{survey_id}/responses", {"answers": {str(qids[1]): "Ж"}}
        )
        self.assertEqual(status, 400)

        status, _ = self.client.request(
            "PUT", f"/api/surveys/{survey_id}/questions", {"questions": questions}
        )
        self.assertEqual(status, 409)

        status, data = self.client.request("GET", f"/api/surveys/{survey_id}/analytics")
        self.assertEqual(status, 200)
        self.assertEqual(data["stats"]["total_responses"], 1)
        age = next(q for q in data["stats"]["questions"] if q["label"] == "Возраст")
        self.assertEqual(age["mean"], 30)
        self.assertFalse(data["ai_enabled"])

        status, data = self.client.request("POST", f"/api/surveys/{survey_id}/analytics")
        self.assertEqual(status, 200)
        self.assertEqual(data["analysis"]["source"], "local")
        self.assertTrue(data["analysis"]["content"])

    def test_members_assignment(self):
        self.client.register_admin()
        status, data = self.client.request("POST", "/api/surveys", {"title": "Команда"})
        survey_id = data["id"]
        status, data = self.client.create_user("Иван", "ivan@example.com", "interviewer")
        self.assertEqual(status, 201)
        user_id = data["id"]

        status, _ = self.client.request("POST", f"/api/surveys/{survey_id}/members", {"user_id": user_id})
        self.assertEqual(status, 200)
        status, data = self.client.request("GET", f"/api/surveys/{survey_id}/members")
        self.assertEqual(len(data["members"]), 2)

        status, _ = self.client.request("DELETE", f"/api/surveys/{survey_id}/members/{user_id}")
        self.assertEqual(status, 200)
        status, data = self.client.request("GET", f"/api/surveys/{survey_id}/members")
        self.assertEqual(len(data["members"]), 1)

    def test_two_factor_login(self):
        admin = self.client
        admin.register_admin()
        status, _ = admin.create_user("Пётр", "petr@example.com", "researcher")
        self.assertEqual(status, 201)
        admin.request("POST", "/api/logout")
        status, data = admin.request("GET", "/api/session")
        self.assertFalse(data["authenticated"])

        guest = Client(self.base)
        status, data = guest.request("POST", "/api/login", {"email": "petr@example.com", "password": "secret123"})
        self.assertEqual(status, 200)
        self.assertTrue(data["twofa_required"])
        self.assertIn("dev_code", data)
        challenge = data["challenge"]
        dev_code = data["dev_code"]

        status, session = guest.request("GET", "/api/session")
        self.assertFalse(session["authenticated"])

        wrong = "999999" if dev_code != "999999" else "111111"
        status, _ = guest.request("POST", "/api/login/verify", {"challenge": challenge, "code": wrong})
        self.assertEqual(status, 401)

        status, data = guest.request(
            "POST", "/api/login/verify", {"challenge": challenge, "code": dev_code}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["email"], "petr@example.com")

        status, data = guest.request("GET", "/api/session")
        self.assertTrue(data["authenticated"])

        status, data = guest.request(
            "POST", "/api/login/verify", {"challenge": challenge, "code": wrong}
        )
        self.assertEqual(status, 400)

    def test_login_rejects_bad_password(self):
        self.client.register_admin()
        self.client.request("POST", "/api/logout")
        status, _ = self.client.request(
            "POST", "/api/login", {"email": "admin@example.com", "password": "wrong-pass"}
        )
        self.assertEqual(status, 401)

    def test_interviewer_cannot_view_responses(self):
        admin = self.client
        admin.register_admin()
        admin.create_user("Интервьюер", "int@example.com", "interviewer")
        status, data = admin.request("POST", "/api/surveys", {"title": "Опрос"})
        survey_id = data["id"]
        admin.request("PUT", f"/api/surveys/{survey_id}/questions",
                      {"questions": [{"label": "Вопрос", "type": "open", "required": True}]})

        worker = Client(self.base)
        _, data = worker.request("POST", "/api/login", {"email": "int@example.com", "password": "secret123"})
        _, data = worker.request("POST", "/api/login/verify", {"challenge": data["challenge"], "code": data["dev_code"]})

        status, data = worker.request("GET", f"/api/surveys/{survey_id}")
        qid = data["survey"]["questions"][0]["id"]
        status, _ = worker.request("POST", f"/api/surveys/{survey_id}/responses", {"answers": {str(qid): "ответ"}})
        self.assertEqual(status, 201)
        status, _ = worker.request("GET", f"/api/surveys/{survey_id}/responses")
        self.assertEqual(status, 403)

    def test_unauthorized_access(self):
        status, _ = self.client.request("GET", "/api/surveys")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
