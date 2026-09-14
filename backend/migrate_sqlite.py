import os
import sqlite3
from pathlib import Path

import psycopg

from backend import main


ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", ROOT / "kidshub.db"))
TARGET_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
TABLES = ("polls", "questions", "submissions", "answers")


def rows(source, query):
    return [tuple(row) for row in source.execute(query).fetchall()]


def migrate():
    if not TARGET_URL:
        raise SystemExit("Вкажіть DATABASE_PUBLIC_URL або DATABASE_URL")
    if not SQLITE_PATH.is_file():
        raise SystemExit(f"SQLite-файл не знайдено: {SQLITE_PATH}")

    main.DATABASE_URL = TARGET_URL
    main.init_db()

    with sqlite3.connect(SQLITE_PATH) as source, psycopg.connect(TARGET_URL) as target:
        existing = target.execute("SELECT COUNT(*) FROM polls").fetchone()[0]
        if existing:
            raise SystemExit("PostgreSQL уже містить опитування. Імпорт скасовано")

        target.cursor().executemany(
            "INSERT INTO polls (id, slug, title, description, thank_you_text, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            rows(source, "SELECT id, slug, title, description, thank_you_text, created_at FROM polls ORDER BY id"),
        )
        target.cursor().executemany(
            "INSERT INTO questions (id, poll_id, position, prompt, description, response_type, options) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows(source, "SELECT id, poll_id, position, prompt, description, response_type, options FROM questions ORDER BY id"),
        )
        target.cursor().executemany(
            "INSERT INTO submissions (id, poll_id, respondent_id, name, created_at) VALUES (%s, %s, %s, %s, %s)",
            rows(source, "SELECT id, poll_id, respondent_id, name, created_at FROM submissions ORDER BY id"),
        )
        target.cursor().executemany(
            "INSERT INTO answers (id, submission_id, question_id, answer) VALUES (%s, %s, %s, %s)",
            rows(source, "SELECT id, submission_id, question_id, answer FROM answers ORDER BY id"),
        )

        for table in TABLES:
            target.execute(
                f"SELECT setval(pg_get_serial_sequence(%s, 'id'), GREATEST(COALESCE(MAX(id), 1), 1), COUNT(*) > 0) FROM {table}",
                (table,),
            )

        counts = {
            table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }

    print(
        f"Перенесено. Опитування: {counts['polls']}; питання: {counts['questions']}; "
        f"анкети: {counts['submissions']}; відповіді: {counts['answers']}"
    )


if __name__ == "__main__":
    migrate()
