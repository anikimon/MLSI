import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_docs  # noqa: E402


class DeckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deck = build_docs.build_deck()
        cls.areas = {area["key"]: area for area in build_docs.AREAS}

    def test_total_and_types(self):
        self.assertEqual(len(self.deck), build_docs.TOTAL)
        self.assertEqual(len(self.deck), 400)
        documents = [doc for doc in self.deck if doc["type"] == "document"]
        presentations = [doc for doc in self.deck if doc["type"] == "presentation"]
        self.assertEqual(len(documents), build_docs.N_DOCS)
        self.assertEqual(len(presentations), build_docs.N_PRESENTATIONS)

    def test_unique_identifiers(self):
        ids = [doc["id"] for doc in self.deck]
        self.assertEqual(sorted(ids), list(range(1, len(self.deck) + 1)))
        cases = [doc["case"] for doc in self.deck]
        self.assertEqual(len(set(cases)), len(cases))

    def test_required_fields(self):
        required = (
            "id", "case", "type", "kind", "title", "sender", "senderType",
            "theme", "themeLabel", "level", "levelLabel", "destKey", "destName",
            "compliant", "body", "slides", "decreeRef", "explanation",
        )
        for doc in self.deck:
            for field in required:
                self.assertIn(field, doc, f"{doc.get('case')} без поля {field}")
            self.assertTrue(doc["title"].strip())
            self.assertTrue(doc["sender"].strip())
            self.assertTrue(doc["destName"].strip())

    def test_routing_is_derivable(self):
        for doc in self.deck:
            area = self.areas[doc["theme"]]
            expected_level = "coord" if doc["senderType"] == "external" else "exec"
            self.assertEqual(doc["level"], expected_level, doc["case"])
            target = area["pr"] if doc["level"] == "coord" else area["dept"]
            self.assertEqual(doc["destKey"], target["key"], doc["case"])
            self.assertEqual(doc["destName"], target["name"], doc["case"])

    def test_documents_have_verdict(self):
        documents = [doc for doc in self.deck if doc["type"] == "document"]
        compliant = [doc for doc in documents if doc["compliant"] is True]
        destructive = [doc for doc in documents if doc["compliant"] is False]
        self.assertGreaterEqual(len(compliant), 100)
        self.assertGreaterEqual(len(destructive), 100)
        for doc in documents:
            self.assertIsInstance(doc["compliant"], bool, doc["case"])
            self.assertTrue(doc["body"].strip(), doc["case"])
            self.assertFalse(doc["slides"], doc["case"])
            self.assertTrue(doc["explanation"].strip(), doc["case"])
            self.assertIn(doc["decreeRef"], ("п. 5", "п. 14, 17"), doc["case"])

    def test_presentations_have_slides(self):
        presentations = [doc for doc in self.deck if doc["type"] == "presentation"]
        for doc in presentations:
            self.assertIsNone(doc["compliant"], doc["case"])
            self.assertFalse(doc["body"], doc["case"])
            self.assertGreaterEqual(len(doc["slides"]), 3, doc["case"])

    def test_all_themes_used(self):
        themes = {doc["theme"] for doc in self.deck}
        self.assertEqual(themes, set(self.areas))


if __name__ == "__main__":
    unittest.main()
