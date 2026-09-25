import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import media_monitor
from media_monitor import collect, summarize


async def _aiter(items):
    for item in items:
        yield item


class FakeEntity:
    title = "Новости региона"
    username = "example"


class FakeMessage:
    def __init__(self, message_id, date, text, views=0, forwards=0):
        self.id = message_id
        self.date = date
        self.message = text
        self.views = views
        self.forwards = forwards


class FakeClient:
    def __init__(self, messages, comments=()):
        self.messages = messages
        self.comments = list(comments)
        self.disconnected = False

    async def connect(self):
        return None

    async def disconnect(self):
        self.disconnected = True

    async def get_entity(self, username):
        return FakeEntity()

    def iter_messages(self, entity, limit=200, reply_to=None):
        return _aiter(self.comments if reply_to else self.messages[:limit])


class SummaryTest(unittest.TestCase):
    def test_telegram_client_uses_optional_socks5_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "test-hash",
                        "TELEGRAM_SESSION_FILE": os.path.join(folder, "telegram.session"),
                        "TELEGRAM_PROXY_HOST": "proxy.example.org", "TELEGRAM_PROXY_PORT": "1080",
                        "TELEGRAM_PROXY_USER": "account", "TELEGRAM_PROXY_PASSWORD": "secret"}
            with patch("telethon.TelegramClient") as client, patch.dict(os.environ, settings):
                media_monitor._telegram_client()
            self.assertEqual(client.call_args.kwargs["proxy"], {
                "proxy_type": "socks5", "addr": "proxy.example.org", "port": 1080,
                "rdns": True, "username": "account", "password": "secret"})
            without_proxy = {**settings, **{key: "" for key in settings if key.startswith("TELEGRAM_PROXY_")}}
            with patch("telethon.TelegramClient") as client, patch.dict(os.environ, without_proxy):
                media_monitor._telegram_client()
            self.assertNotIn("proxy", client.call_args.kwargs)

    def test_telegram_proxy_rejects_incomplete_configuration(self):
        for settings in ({"TELEGRAM_PROXY_HOST": "proxy.example.org"},
                         {"TELEGRAM_PROXY_PORT": "1080"},
                         {"TELEGRAM_PROXY_HOST": "proxy.example.org", "TELEGRAM_PROXY_PORT": "invalid"},
                         {"TELEGRAM_PROXY_HOST": "proxy.example.org", "TELEGRAM_PROXY_PORT": "65536"},
                         {"TELEGRAM_PROXY_HOST": "proxy.example.org", "TELEGRAM_PROXY_PORT": "1080",
                          "TELEGRAM_PROXY_PASSWORD": "secret"}):
            with self.subTest(settings=tuple(settings)):
                with patch.dict(os.environ, settings, clear=True):
                    with self.assertRaises(ValueError) as error:
                        media_monitor.telegram_proxy()
                self.assertNotIn("secret", str(error.exception))

    def test_telegram_status_does_not_block_when_collector_is_busy(self):
        with patch.dict(media_monitor.os.environ, {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "placeholder"}):
            with media_monitor.TELEGRAM_LOCK:
                result = media_monitor.telegram_status()
        self.assertTrue(result["configured"])
        self.assertFalse(result["authorized"])
        self.assertIn("занят", result["message"])

    def test_telegram_status_times_out_and_releases_lock(self):
        async def timeout_authorization(client):
            raise TimeoutError()

        with patch.dict(media_monitor.os.environ, {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "placeholder"}), \
                patch("media_monitor._telegram_client", return_value=object()), \
                patch("media_monitor._telegram_authorized", side_effect=timeout_authorization):
            result = media_monitor.telegram_status()
        self.assertIn("Таймаут", result["message"])
        self.assertTrue(media_monitor.TELEGRAM_LOCK.acquire(blocking=False))
        media_monitor.TELEGRAM_LOCK.release()

    def test_rising_terms_are_observed_overall(self):
        titles = ["Economy and markets", "Economy and trade", "Markets improve",
                  "Climate flooding report", "Climate flooding warning", "Climate flooding response"]
        rows = [{"id": index, "source": "telegram", "title": title, "excerpt": "",
                 "url": f"https://t.me/example/{index}"}
                for index, title in enumerate(titles, 1)]
        summary = summarize(rows)
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["telegram"], 6)
        self.assertIn("climate", [term["word"] for term in summary["overall"]["trending_terms"]])

    def test_direction_only_from_explicit_phrase(self):
        rows = [{"id": 1, "source": "telegram", "title": "Russia attacks Ukraine",
                 "excerpt": "", "url": "https://t.me/example/1"},
                {"id": 2, "source": "telegram", "title": "US warns Russia after attack",
                 "excerpt": "", "url": "https://t.me/example/2"}]
        items = summarize(rows)["overall"]["conflict_mentions"]
        self.assertEqual(items[0]["direction"], {"actor_mentioned": "Россия", "target_mentioned": "Украина"})
        self.assertIsNone(items[1]["direction"])

    def test_telegram_agent_filters_messages_by_lookback(self):
        now = datetime.now(timezone.utc)
        messages = [FakeMessage(3, now, "Свежий пост", views=10, forwards=2),
                    FakeMessage(2, now - timedelta(hours=2), "Пост в окне"),
                    FakeMessage(1, now - timedelta(hours=40), "Старый пост")]
        client = FakeClient(messages)
        with patch("media_monitor._telegram_client", return_value=client):
            records = asyncio.run(media_monitor._telegram_agent("события", "ЛНР", ["@example"], lookback_hours=6))
        posts = [record["url"] for record in records if record["item_type"] == "post"]
        self.assertEqual(posts, ["https://t.me/example/3", "https://t.me/example/2"])
        self.assertTrue(client.disconnected)

    def test_telegram_agent_collects_comments_in_window(self):
        now = datetime.now(timezone.utc)
        messages = [FakeMessage(5, now, "Пост с обсуждением")]
        comments = [FakeMessage(11, now - timedelta(hours=1), "Комментарий по теме"),
                    FakeMessage(10, now - timedelta(hours=30), "Старый комментарий")]
        client = FakeClient(messages, comments)
        with patch("media_monitor._telegram_client", return_value=client):
            records = asyncio.run(media_monitor._telegram_agent("тема", "", ["@example"], lookback_hours=6))
        comments_found = [record for record in records if record["item_type"] == "comment"]
        self.assertEqual(len(comments_found), 1)
        self.assertEqual(comments_found[0]["url"], "https://t.me/example/5?comment=11")

    def test_collect_stores_only_telegram_records(self):
        calls = []

        def fake_telegram(question, region, channels, lookback_hours):
            calls.append((question, region, channels, lookback_hours))
            return [{"source": "telegram", "title": "Обсуждение события",
                     "excerpt": "Публичный пост канала", "url": "https://t.me/example/42",
                     "published": "2026-01-01", "item_type": "post", "community": "Канал",
                     "author": "Канал", "engagement": 7}]

        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(f"{directory}/media.db")
            db.execute("""CREATE TABLE media_items (
                id INTEGER PRIMARY KEY, monitor_id INTEGER, source TEXT, country TEXT, title TEXT, excerpt TEXT,
                url TEXT UNIQUE, published TEXT, collected_at TEXT, item_type TEXT, author TEXT, community TEXT, engagement INTEGER)""")
            monitor = {"id": 1, "question": "события", "region": "ЛНР", "lookback_hours": 12,
                       "telegram_channels": "[\"https://t.me/example\"]"}
            added, result = collect(db, monitor, fetch_telegram=fake_telegram)
            db.commit()
            row = db.execute("SELECT source, url, community FROM media_items").fetchone()
            db.close()
        self.assertEqual(calls, [("события", "ЛНР", ["https://t.me/example"], 12)])
        self.assertEqual(added, 1)
        self.assertEqual(result["social"], "telegram: Добавлено: 1")
        self.assertEqual(row, ("telegram", "https://t.me/example/42", "Канал"))

    def test_collect_without_channels_reports_configuration(self):
        def unexpected(*args):
            raise AssertionError("collector must not run")

        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(f"{directory}/media.db")
            db.execute("""CREATE TABLE media_items (id INTEGER PRIMARY KEY, monitor_id INTEGER, source TEXT, country TEXT,
                title TEXT, excerpt TEXT, url TEXT UNIQUE, published TEXT, collected_at TEXT, item_type TEXT,
                author TEXT, community TEXT, engagement INTEGER)""")
            monitor = {"id": 1, "question": "события", "region": "ЛНР", "lookback_hours": 24, "telegram_channels": "[]"}
            added, result = collect(db, monitor, fetch_telegram=unexpected)
            db.close()
        self.assertEqual(added, 0)
        self.assertIn("Не выбраны Telegram-каналы", result["telegram"])


if __name__ == "__main__":
    unittest.main()
