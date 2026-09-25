import json
import unittest

from digital_focus import parse_replies, retrieve


class DigitalFocusTest(unittest.TestCase):
    def test_retrieval_prefers_relevant_comments_and_rejects_fabricated_sources(self):
        sources = [{"id": 1, "body": "В парке не хватает деревьев и тени"},
                   {"id": 2, "body": "Открыли новый автобусный маршрут"},
                   {"id": 3, "body": "Нужны деревья, чтобы была тень"}]
        evidence = retrieve(sources, "Почему мало деревьев и тени?", "", [])
        self.assertEqual({row["id"] for row in evidence[:2]}, {1, 3})
        good = json.dumps({"replies": [{"speaker": 1, "text": "Нужна тень", "source_ids": [3]},
                                      {"speaker": 2, "text": "Согласен", "source_ids": [1]}]})
        self.assertEqual(len(parse_replies(good, 2, evidence)), 2)
        forged = json.dumps({"replies": [{"speaker": 1, "text": "Нужна тень", "source_ids": [999]},
                                        {"speaker": 2, "text": "Согласен", "source_ids": [1]}]})
        with self.assertRaises(ValueError):
            parse_replies(forged, 2, evidence)


if __name__ == "__main__":
    unittest.main()
