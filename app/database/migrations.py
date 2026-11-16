from __future__ import annotations

from pathlib import Path

from .rustydb import RustyDB


def _statements(sql: str) -> list[str]:
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in without_comments.split(";") if statement.strip()]


def apply_migrations(database: RustyDB, directory: Path) -> list[str]:
    database.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {
        row["version"]
        for row in database.fetch_all("SELECT version FROM schema_migrations")
    }
    completed: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in applied:
            continue
        import time

        with database.transaction() as connection:
            for statement in _statements(path.read_text(encoding="utf-8")):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                (path.name, int(time.time())),
            )
        completed.append(path.name)
    return completed

