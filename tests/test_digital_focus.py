import json
import unittest

from digital_focus import make_turn_payload, parse_replies, parse_suggestions, retrieve, suggest_personas_payload, validate_personas


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

    def test_twelve_personas_have_individual_prompts_and_validated_suggestions(self):
        personas = [{"name": f"Позиция {i}", "prompt": f"Считает важным вопрос {i}"} for i in range(1, 13)]
        self.assertEqual(validate_personas(personas, 12), personas)
        evidence = [{"id": 1, "body": "Нужны деревья"}]
        payload = make_turn_payload("Гайд", personas, [], "Что улучшить?", evidence)
        self.assertEqual(payload["max_tokens"], 6000)
        request = json.loads(payload["messages"][1]["content"])
        self.assertEqual(request["personas"][11]["prompt"], "Считает важным вопрос 12")
        suggestion = suggest_personas_payload(["Нужны деревья"], 12, "Гайд")
        self.assertIn("Ровно 12", suggestion["messages"][0]["content"])
        self.assertEqual(parse_suggestions(json.dumps({"personas": personas}), 12), personas)
        with self.assertRaises(ValueError):
            validate_personas(personas + personas[:1])
        with self.assertRaises(ValueError):
            validate_personas([personas[0], personas[0]])
        with self.assertRaises(ValueError):
            parse_suggestions(json.dumps({"personas": personas[:3]}), 12)
        with self.assertRaises(ValueError):
            parse_suggestions(json.dumps({"personas": [personas[0], {"name": "Новая", "prompt": ""}]}), 2)


if __name__ == "__main__":
    unittest.main()
