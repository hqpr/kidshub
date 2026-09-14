import sys
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from backend.main import PollCreate, create_poll, init_db


def import_poll(path):
    try:
        payload = PollCreate.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise SystemExit(str(error)) from error

    init_db()
    try:
        create_poll(payload)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        print(f"Опитування /{payload.slug}/ вже існує")
        return
    print(f"Опитування /{payload.slug}/ імпортовано: {len(payload.questions)} питань")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Використання: python -m backend.import_poll data/опитування.json")
    import_poll(sys.argv[1])
