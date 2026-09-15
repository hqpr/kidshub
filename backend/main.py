import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from threading import Lock
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator, model_validator


ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/kidshub")
DATABASE_SCHEMA = os.getenv("DATABASE_SCHEMA")
ADMIN_KEY = os.getenv("ADMIN_KEY")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
DEFAULT_THANK_YOU = "Дякуємо! Ваші відповіді збережено."
FAILED_LOGINS = {}
LOGIN_LOCK = Lock()
LOGIN_LIMIT = 5
LOGIN_WINDOW = 900


def connect():
    if DATABASE_SCHEMA and not SCHEMA_RE.fullmatch(DATABASE_SCHEMA):
        raise RuntimeError("Некоректна назва схеми PostgreSQL")
    options = f"-c search_path={DATABASE_SCHEMA}" if DATABASE_SCHEMA else None
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5, options=options)


def init_db():
    with connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS polls (
                id BIGSERIAL PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                thank_you_text TEXT NOT NULL DEFAULT 'Дякуємо! Ваші відповіді збережено.',
                created_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
                id BIGSERIAL PRIMARY KEY,
                poll_id BIGINT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                response_type TEXT NOT NULL CONSTRAINT questions_response_type_check CHECK(response_type IN ('single', 'multiple', 'text', 'rating')),
                options TEXT NOT NULL DEFAULT '[]',
                UNIQUE(poll_id, position)
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id BIGSERIAL PRIMARY KEY,
                poll_id BIGINT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                respondent_id TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(poll_id, respondent_id)
            );
            CREATE TABLE IF NOT EXISTS answers (
                id BIGSERIAL PRIMARY KEY,
                submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                answer TEXT NOT NULL,
                UNIQUE(submission_id, question_id)
            );
            CREATE INDEX IF NOT EXISTS submissions_poll_id_idx ON submissions(poll_id);
            CREATE INDEX IF NOT EXISTS answers_question_id_idx ON answers(question_id);
            ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_response_type_check;
            ALTER TABLE questions ADD CONSTRAINT questions_response_type_check CHECK(response_type IN ('single', 'multiple', 'text', 'rating'));
            """
        )


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Тиц!", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > 65_536:
        return Response("Запит завеликий", status_code=413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:8000 http://localhost:8000; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def require_admin(request: Request, x_admin_key: str = Header(default="", max_length=128)):
    if not ADMIN_KEY or len(ADMIN_KEY) < 12:
        raise HTTPException(503, "ADMIN_KEY має містити щонайменше 12 символів")
    address = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with LOGIN_LOCK:
        attempts = [attempt for attempt in FAILED_LOGINS.get(address, []) if now - attempt < LOGIN_WINDOW]
        if len(attempts) >= LOGIN_LIMIT:
            retry_after = max(1, round(LOGIN_WINDOW - (now - attempts[0])))
            raise HTTPException(429, "Забагато невдалих спроб. Спробуйте пізніше", headers={"Retry-After": str(retry_after)})
        if not secrets.compare_digest(x_admin_key, ADMIN_KEY):
            attempts.append(now)
            FAILED_LOGINS[address] = attempts
            if len(attempts) >= LOGIN_LIMIT:
                raise HTTPException(429, "Забагато невдалих спроб. Спробуйте через 15 хвилин", headers={"Retry-After": str(LOGIN_WINDOW)})
            raise HTTPException(401, "Неправильний ключ адміністратора")
        FAILED_LOGINS.pop(address, None)


class QuestionCreate(BaseModel):
    prompt: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=500)
    response_type: Literal["single", "multiple", "text", "rating"]
    options: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("prompt", "description")
    @classmethod
    def clean_text(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def valid_options(self):
        self.options = [option.strip() for option in self.options if option.strip()]
        if self.response_type in {"single", "multiple"} and len(self.options) < 2:
            raise ValueError("Додайте щонайменше два варіанти")
        if any(len(option) > 120 for option in self.options):
            raise ValueError("Варіант відповіді задовгий")
        if len(set(self.options)) != len(self.options):
            raise ValueError("Варіанти не мають повторюватися")
        if self.response_type == "text":
            self.options = []
        if self.response_type == "rating":
            if not self.options:
                self.options = ["5"]
            if self.options not in [["5"], ["10"]]:
                raise ValueError("Шкала оцінки має бути від 1 до 5 або від 1 до 10")
        return self


class PollCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=500)
    thank_you_text: str = Field(default=DEFAULT_THANK_YOU, min_length=3, max_length=1000)
    questions: list[QuestionCreate] = Field(min_length=1, max_length=30)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value):
        value = value.strip().lower()
        if not SLUG_RE.fullmatch(value) or value in {"admin", "api", "assets"}:
            raise ValueError("Slug: лише латиниця, цифри й дефіси")
        return value

    @field_validator("title", "description", "thank_you_text")
    @classmethod
    def clean_text(cls, value):
        return value.strip()


class PollUpdate(BaseModel):
    slug: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=500)
    thank_you_text: str = Field(default=DEFAULT_THANK_YOU, min_length=3, max_length=1000)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value):
        value = value.strip().lower()
        if not SLUG_RE.fullmatch(value) or value in {"admin", "api", "assets"}:
            raise ValueError("Slug: лише латиниця, цифри й дефіси")
        return value

    @field_validator("title", "description", "thank_you_text")
    @classmethod
    def clean_text(cls, value):
        return value.strip()


class AnswerCreate(BaseModel):
    question_id: int
    selected: list[str] = Field(default_factory=list, max_length=10)
    rating: int | None = Field(default=None, ge=1, le=10, strict=True)
    text: str | None = Field(default=None, max_length=1000)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value):
        return value.strip() or None if value is not None else None


class SubmissionCreate(BaseModel):
    respondent_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    name: str | None = Field(default=None, max_length=80)
    answers: list[AnswerCreate] = Field(min_length=1, max_length=30)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return value.strip() or None if value is not None else None


def question_dict(row):
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "description": row["description"],
        "response_type": row["response_type"],
        "options": json.loads(row["options"]),
    }


def poll_dict(db, row):
    questions = db.execute(
        "SELECT * FROM questions WHERE poll_id = %s ORDER BY position", (row["id"],)
    ).fetchall()
    return {
        "slug": row["slug"],
        "title": row["title"],
        "description": row["description"],
        "thank_you_text": row["thank_you_text"],
        "questions": [question_dict(question) for question in questions],
        "created_at": row["created_at"],
    }


def find_poll(db, slug):
    row = db.execute("SELECT * FROM polls WHERE slug = %s", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "Опитування не знайдено")
    return row


def validated_answer(question, payload):
    options = json.loads(question["options"])
    if question["response_type"] == "rating":
        rating_max = int(options[0]) if options else 5
        if payload.rating is None or payload.rating > rating_max:
            raise HTTPException(422, f"Поставте оцінку: {question['prompt']}")
        return json.dumps({"rating": payload.rating, "text": payload.text}, ensure_ascii=False)
    if question["response_type"] == "text":
        if not payload.text:
            raise HTTPException(422, f"Напишіть відповідь: {question['prompt']}")
        return payload.text
    selected = list(dict.fromkeys(payload.selected))
    if not selected or any(item not in options for item in selected):
        raise HTTPException(422, f"Оберіть відповідь: {question['prompt']}")
    if question["response_type"] == "single" and len(selected) != 1:
        raise HTTPException(422, f"Оберіть один варіант: {question['prompt']}")
    return json.dumps(selected, ensure_ascii=False)


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/latest-poll")
def latest_poll():
    with connect() as db:
        poll = db.execute("SELECT slug FROM polls ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    if not poll:
        raise HTTPException(404, "Опитувань поки немає")
    return {"slug": poll["slug"]}


@app.get("/api/polls/{slug}")
def get_poll(slug: str):
    with connect() as db:
        return poll_dict(db, find_poll(db, slug))


@app.post("/api/polls/{slug}/responses", status_code=201)
def submit_response(slug: str, payload: SubmissionCreate):
    try:
        with connect() as db:
            poll = find_poll(db, slug)
            questions = db.execute(
                "SELECT * FROM questions WHERE poll_id = %s ORDER BY position", (poll["id"],)
            ).fetchall()
            received = {answer.question_id: answer for answer in payload.answers}
            if len(received) != len(payload.answers) or set(received) != {row["id"] for row in questions}:
                raise HTTPException(422, "Дайте відповідь на кожне питання")
            values = [(row["id"], validated_answer(row, received[row["id"]])) for row in questions]
            submission_id = db.execute(
                "INSERT INTO submissions (poll_id, respondent_id, name, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                (poll["id"], payload.respondent_id, payload.name, datetime.now(timezone.utc)),
            ).fetchone()["id"]
            db.cursor().executemany(
                "INSERT INTO answers (submission_id, question_id, answer) VALUES (%s, %s, %s)",
                [(submission_id, question_id, answer) for question_id, answer in values],
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Ви вже відповіли на це опитування")
    return {"ok": True}


@app.get("/api/admin/polls", dependencies=[Depends(require_admin)])
def list_polls():
    with connect() as db:
        rows = db.execute(
            "SELECT p.*, COUNT(s.id) AS response_count FROM polls p LEFT JOIN submissions s ON s.poll_id = p.id GROUP BY p.id ORDER BY p.id DESC"
        ).fetchall()
        return [
            {**poll_dict(db, row), "response_count": row["response_count"]}
            for row in rows
        ]


@app.post("/api/admin/polls", dependencies=[Depends(require_admin)], status_code=201)
def create_poll(payload: PollCreate):
    try:
        with connect() as db:
            poll_id = db.execute(
                "INSERT INTO polls (slug, title, description, thank_you_text, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (payload.slug, payload.title, payload.description, payload.thank_you_text, datetime.now(timezone.utc)),
            ).fetchone()["id"]
            db.cursor().executemany(
                "INSERT INTO questions (poll_id, position, prompt, description, response_type, options) VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        poll_id,
                        position,
                        question.prompt,
                        question.description,
                        question.response_type,
                        json.dumps(question.options, ensure_ascii=False),
                    )
                    for position, question in enumerate(payload.questions)
                ],
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Такий slug уже зайнятий")
    return {"slug": payload.slug}


@app.patch("/api/admin/polls/{slug}", dependencies=[Depends(require_admin)])
def update_poll(slug: str, payload: PollUpdate):
    try:
        with connect() as db:
            updated = db.execute(
                "UPDATE polls SET slug = %s, title = %s, description = %s, thank_you_text = %s WHERE slug = %s",
                (payload.slug, payload.title, payload.description, payload.thank_you_text, slug),
            )
            if not updated.rowcount:
                raise HTTPException(404, "Форму не знайдено")
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Такий slug уже зайнятий")
    return {"slug": payload.slug}


@app.delete("/api/admin/polls/{slug}", dependencies=[Depends(require_admin)], status_code=204)
def delete_poll(slug: str):
    with connect() as db:
        deleted = db.execute("DELETE FROM polls WHERE slug = %s", (slug,))
        if not deleted.rowcount:
            raise HTTPException(404, "Форму не знайдено")
    return Response(status_code=204)


@app.post("/api/admin/polls/{slug}/questions", dependencies=[Depends(require_admin)], status_code=201)
def add_question(slug: str, payload: QuestionCreate):
    with connect() as db:
        poll = find_poll(db, slug)
        position = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM questions WHERE poll_id = %s", (poll["id"],)
        ).fetchone()["position"]
        question_id = db.execute(
            "INSERT INTO questions (poll_id, position, prompt, description, response_type, options) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (poll["id"], position, payload.prompt, payload.description, payload.response_type, json.dumps(payload.options, ensure_ascii=False)),
        ).fetchone()["id"]
    return {"id": question_id}


@app.patch("/api/admin/questions/{question_id}", dependencies=[Depends(require_admin)])
def update_question(question_id: int, payload: QuestionCreate):
    with connect() as db:
        question = db.execute("SELECT * FROM questions WHERE id = %s", (question_id,)).fetchone()
        if not question:
            raise HTTPException(404, "Питання не знайдено")
        answer_count = db.execute("SELECT COUNT(*) AS count FROM answers WHERE question_id = %s", (question_id,)).fetchone()["count"]
        options = json.dumps(payload.options, ensure_ascii=False)
        if answer_count and (payload.response_type != question["response_type"] or options != question["options"]):
            raise HTTPException(409, "Після отримання відповідей можна змінити лише текст питання та пояснення")
        db.execute(
            "UPDATE questions SET prompt = %s, description = %s, response_type = %s, options = %s WHERE id = %s",
            (payload.prompt, payload.description, payload.response_type, options, question_id),
        )
    return {"id": question_id}


@app.delete("/api/admin/questions/{question_id}", dependencies=[Depends(require_admin)], status_code=204)
def delete_question(question_id: int):
    with connect() as db:
        question = db.execute("SELECT * FROM questions WHERE id = %s", (question_id,)).fetchone()
        if not question:
            raise HTTPException(404, "Питання не знайдено")
        count = db.execute("SELECT COUNT(*) AS count FROM questions WHERE poll_id = %s", (question["poll_id"],)).fetchone()["count"]
        if count == 1:
            raise HTTPException(409, "У формі має залишитися хоча б одне питання")
        db.execute("DELETE FROM questions WHERE id = %s", (question_id,))
        db.execute(
            "UPDATE questions SET position = position + 1000 WHERE poll_id = %s AND position > %s",
            (question["poll_id"], question["position"]),
        )
        db.execute(
            "UPDATE questions SET position = position - 1001 WHERE poll_id = %s AND position > %s",
            (question["poll_id"], question["position"] + 1000),
        )
    return Response(status_code=204)


@app.get("/api/admin/polls/{slug}/stats", dependencies=[Depends(require_admin)])
def poll_stats(slug: str):
    with connect() as db:
        poll = find_poll(db, slug)
        submissions = db.execute(
            "SELECT * FROM submissions WHERE poll_id = %s", (poll["id"],)
        ).fetchall()
        questions = db.execute(
            "SELECT * FROM questions WHERE poll_id = %s ORDER BY position", (poll["id"],)
        ).fetchall()
        question_stats = []
        for question in questions:
            rows = db.execute(
                "SELECT a.answer, s.name, s.created_at FROM answers a JOIN submissions s ON s.id = a.submission_id WHERE a.question_id = %s ORDER BY s.id DESC",
                (question["id"],),
            ).fetchall()
            item = {**question_dict(question), "answer_count": len(rows), "distribution": [], "answers": [], "average_rating": None}
            if question["response_type"] == "text":
                item["answers"] = [dict(row) for row in rows]
            elif question["response_type"] == "rating":
                ratings = []
                rating_max = int(json.loads(question["options"])[0]) if question["options"] != "[]" else 5
                counts = {score: 0 for score in range(rating_max, 0, -1)}
                for row in rows:
                    answer = json.loads(row["answer"])
                    rating = answer["rating"]
                    ratings.append(rating)
                    counts[rating] += 1
                    if answer.get("text"):
                        item["answers"].append({**dict(row), "answer": answer["text"], "rating": rating})
                item["average_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else None
                item["distribution"] = [
                    {
                        "option": str(score),
                        "count": count,
                        "percent": round(count * 100 / len(rows)) if rows else 0,
                    }
                    for score, count in counts.items()
                ]
            else:
                counts = {option: 0 for option in json.loads(question["options"])}
                for row in rows:
                    for option in json.loads(row["answer"]):
                        counts[option] += 1
                item["distribution"] = [
                    {
                        "option": option,
                        "count": count,
                        "percent": round(count * 100 / len(rows)) if rows else 0,
                    }
                    for option, count in counts.items()
                ]
            question_stats.append(item)
    return {
        "total": len(submissions),
        "named": sum(1 for row in submissions if row["name"]),
        "questions": question_stats,
    }


def social_meta(document, values):
    for key, value in values.items():
        safe_value = escape(value, quote=True)
        document = re.sub(
            rf'(<(?:meta|link)\b[^>]*data-dynamic="{key}"[^>]*(?:content|href)=")[^"]*(")',
            rf'\g<1>{safe_value}\2',
            document,
        )
    if "title" in values:
        document = re.sub(r"<title>.*?</title>", f"<title>{escape(values['title'])}</title>", document, count=1)
    return document


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/og-cover.png", include_in_schema=False)
    def og_cover():
        return FileResponse(DIST / "og-cover.png", media_type="image/png")

    @app.get("/kids-hub-logo.png", include_in_schema=False)
    def kids_hub_logo():
        return FileResponse(DIST / "kids-hub-logo.png", media_type="image/png")

    @app.get("/{path:path}")
    def frontend(path: str, request: Request):
        origin = str(request.base_url).rstrip("/")
        values = {
            "url": str(request.url).split("?", 1)[0],
            "image": f"{origin}/kids-hub-logo.png",
        }
        clean_path = path.strip("/")
        if clean_path and "/" not in clean_path:
            with connect() as db:
                poll = db.execute("SELECT title, description FROM polls WHERE slug = %s", (clean_path,)).fetchone()
            if poll:
                values["title"] = f"{poll['title']} — Тиц!"
                values["description"] = poll["description"] or "Поділіться своєю думкою в короткому опитуванні Kids Hub."
        document = social_meta((DIST / "index.html").read_text(), values)
        return HTMLResponse(document, headers={"Cache-Control": "public, max-age=60"})
