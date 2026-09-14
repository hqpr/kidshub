import os
import secrets
import unittest
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql

import backend.main


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL не задано")
class AppTest(unittest.TestCase):
    def setUp(self):
        self.schema = f"test_{secrets.token_hex(8)}"
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.main = backend.main
        self.main.DATABASE_URL = TEST_DATABASE_URL
        self.main.DATABASE_SCHEMA = self.schema
        self.main.ADMIN_KEY = "secret-key-123"
        self.main.FAILED_LOGINS.clear()
        self.client_context = TestClient(self.main.app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-Admin-Key": "secret-key-123"}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.main.DATABASE_SCHEMA = None

    def test_form_flow(self):
        self.assertEqual(self.client.get("/health").json(), {"ok": True})
        created = self.client.post(
            "/api/admin/polls",
            headers=self.headers,
            json={
                "slug": "summer",
                "title": "Літня подорож",
                "description": "Обираймо разом",
                "questions": [
                    {
                        "prompt": "Куди їдемо?",
                        "description": "",
                        "response_type": "multiple",
                        "options": ["Море", "Гори"],
                    },
                    {
                        "prompt": "Що важливо?",
                        "description": "",
                        "response_type": "text",
                        "options": [],
                    },
                    {
                        "prompt": "Як вам подорож?",
                        "description": "",
                        "response_type": "rating",
                        "options": ["10"],
                    },
                ],
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/latest-poll").json()["slug"], "summer")
        questions = self.client.get("/api/polls/summer").json()["questions"]
        response = self.client.post(
            "/api/polls/summer/responses",
            json={
                "respondent_id": "12345678-1234-1234-1234-123456789012",
                "name": "Оля",
                "answers": [
                    {"question_id": questions[0]["id"], "selected": ["Море", "Гори"]},
                    {"question_id": questions[1]["id"], "text": "Сонце"},
                    {"question_id": questions[2]["id"], "rating": 8, "text": "Майже ідеально"},
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        stats = self.client.get("/api/admin/polls/summer/stats", headers=self.headers).json()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["named"], 1)
        self.assertEqual([item["count"] for item in stats["questions"][0]["distribution"]], [1, 1])
        self.assertEqual(stats["questions"][1]["answers"][0]["answer"], "Сонце")
        self.assertEqual(stats["questions"][2]["average_rating"], 8)
        self.assertEqual([item["count"] for item in stats["questions"][2]["distribution"]], [0, 0, 1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(stats["questions"][2]["answers"][0]["answer"], "Майже ідеально")
        question_id = questions[0]["id"]
        edited = self.client.patch(
            f"/api/admin/questions/{question_id}",
            headers=self.headers,
            json={"prompt": "Куди саме їдемо?", "response_type": "multiple", "options": ["Море", "Гори"]},
        )
        self.assertEqual(edited.status_code, 200)
        changed_options = self.client.patch(
            f"/api/admin/questions/{question_id}",
            headers=self.headers,
            json={"prompt": "Куди саме їдемо?", "response_type": "multiple", "options": ["Озеро", "Гори"]},
        )
        self.assertEqual(changed_options.status_code, 409)

    def test_requires_every_answer(self):
        self.assertEqual(self.client.get("/api/latest-poll").status_code, 404)
        self.client.post(
            "/api/admin/polls",
            headers=self.headers,
            json={
                "slug": "short-form",
                "title": "Швидка форма",
                "questions": [
                    {"prompt": "Готові?", "response_type": "single", "options": ["Так", "Ні"]}
                ],
            },
        )
        response = self.client.post(
            "/api/polls/short-form/responses",
            json={"respondent_id": "12345678-1234-1234-1234-123456789012", "answers": []},
        )
        self.assertEqual(response.status_code, 422)

    def test_admin_crud_and_bruteforce_limit(self):
        for attempt in range(5):
            response = self.client.get("/api/admin/polls", headers={"X-Admin-Key": "wrong-password"})
            self.assertEqual(response.status_code, 429 if attempt == 4 else 401)
        blocked = self.client.get("/api/admin/polls", headers=self.headers)
        self.assertEqual(blocked.status_code, 429)
        self.main.FAILED_LOGINS.clear()
        self.client.post(
            "/api/admin/polls",
            headers=self.headers,
            json={
                "slug": "editable",
                "title": "Форма для змін",
                "questions": [{"prompt": "Перше?", "response_type": "single", "options": ["Так", "Ні"]}],
            },
        )
        added = self.client.post(
            "/api/admin/polls/editable/questions",
            headers=self.headers,
            json={"prompt": "Друге?", "response_type": "text"},
        )
        self.assertEqual(added.status_code, 201)
        self.assertEqual(self.client.delete(f"/api/admin/questions/{added.json()['id']}", headers=self.headers).status_code, 204)
        renamed = self.client.patch(
            "/api/admin/polls/editable",
            headers=self.headers,
            json={"slug": "edited", "title": "Оновлена форма", "description": "Готово"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(self.client.delete("/api/admin/polls/edited", headers=self.headers).status_code, 204)


class SocialMetaTest(unittest.TestCase):
    def test_social_meta_is_dynamic_and_escaped(self):
        document = '<meta data-dynamic="title" content="old"><meta data-dynamic="url" content="old"><title>old</title>'
        rendered = backend.main.social_meta(document, {"title": 'Літо & "діти"', "url": "https://example.com/summer/"})
        self.assertIn('content="Літо &amp; &quot;діти&quot;"', rendered)
        self.assertIn('content="https://example.com/summer/"', rendered)
        self.assertIn("<title>Літо &amp; &quot;діти&quot;</title>", rendered)

    def test_summer_poll_json(self):
        payload = backend.main.PollCreate.model_validate_json(
            Path("data/summer-feedback.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload.slug, "summer-feedback")
        self.assertEqual(len(payload.questions), 19)
        self.assertEqual(payload.questions[2].response_type, "multiple")

    def test_rating_respects_question_scale(self):
        question = {"response_type": "rating", "options": '["5"]', "prompt": "Оцініть"}
        with self.assertRaises(backend.main.HTTPException) as error:
            backend.main.validated_answer(question, backend.main.AnswerCreate(question_id=1, rating=6))
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
