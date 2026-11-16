from pathlib import Path

import pytest

from app.database.migrations import apply_migrations

pytestmark = pytest.mark.integration


def test_all_matcharr_migrations_apply_to_real_rustydb(database):
    completed = apply_migrations(
        database, Path(__file__).resolve().parents[2] / "migrations"
    )
    assert "001_initial.sql" in completed or database.fetch_one(
        "SELECT version FROM schema_migrations WHERE version = %s",
        ("001_initial.sql",),
    )
    tables = {next(iter(row.values())) for row in database.fetch_all("SHOW TABLES")}
    assert {
        "participants",
        "movies",
        "rooms",
        "room_members",
        "selection_sessions",
        "session_members",
        "session_movies",
        "session_member_state",
        "session_finish_votes",
        "room_lobbies",
        "lobby_members",
        "lobby_suggestions",
        "session_final_votes",
        "swipes",
        "matches",
        "match_members",
        "plex_sync_runs",
    }.issubset(tables)
