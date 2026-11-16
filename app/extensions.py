from typing import Any

db: Any = None


def set_db(database: Any) -> None:
    global db
    db = database

