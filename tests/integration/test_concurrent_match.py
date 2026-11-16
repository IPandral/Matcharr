from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import uuid

import pytest

from app.matching.services import match_details, record_swipe
from app.database.migrations import apply_migrations

pytestmark = pytest.mark.integration


def test_concurrent_final_swipes_create_one_match(database):
    apply_migrations(database, Path(__file__).resolve().parents[2] / "migrations")
    suffix = uuid.uuid4().hex[:8]
    room_id = f"room-{suffix}"
    session_id = f"session-{suffix}"
    movie_id = f"movie-{suffix}"
    participant_ids = [f"person-a-{suffix}", f"person-b-{suffix}"]
    now = int(time.time())
    for participant_id, display_name in zip(participant_ids, ("Ada", "Grace")):
        database.execute(
            "INSERT INTO participants (id, display_name, created_at, last_seen_at) "
            "VALUES (%s, %s, %s, %s)",
            (participant_id, display_name, now, now),
        )
    database.execute(
        "INSERT INTO movies "
        "(id, plex_rating_key, title, release_year, summary, poster_path, background_path, "
        "duration_minutes, content_rating, audience_rating, critic_rating, genres_json, "
        "date_added, view_count, watched, plex_watch_url, metadata_json, created_at, updated_at) "
        "VALUES (%s, %s, 'Arrival', 2016, '', NULL, NULL, 116, 'PG-13', 8.1, 7.9, "
        "'[]', %s, 0, FALSE, NULL, '{}', %s, %s)",
        (movie_id, f"plex-{suffix}", now, now, now),
    )
    database.execute(
        "INSERT INTO rooms "
        "(id, owner_participant_id, name, invite_code, is_archived, include_watched, "
        "default_filters_json, created_at, updated_at) "
        "VALUES (%s, %s, 'Films', %s, FALSE, FALSE, '{}', %s, %s)",
        (room_id, participant_ids[0], f"CODE{suffix}".upper(), now, now),
    )
    membership_ids = []
    for index, participant_id in enumerate(participant_ids):
        membership_id = f"membership-{index}-{suffix}"
        membership_ids.append(membership_id)
        database.execute(
            "INSERT INTO room_members "
            "(id, room_id, participant_id, display_name, role, is_active, joined_at, left_at) "
            "VALUES (%s, %s, %s, %s, %s, TRUE, %s, NULL)",
            (
                membership_id,
                room_id,
                participant_id,
                "Ada" if index == 0 else "Grace",
                "owner" if index == 0 else "member",
                now,
            ),
        )
    database.execute(
        "INSERT INTO selection_sessions "
        "(id, room_id, created_by_participant_id, status, filters_json, started_at, completed_at) "
        "VALUES (%s, %s, %s, 'active', '{}', %s, NULL)",
        (session_id, room_id, participant_ids[0], now),
    )
    for index, participant_id in enumerate(participant_ids):
        database.execute(
            "INSERT INTO session_members "
            "(id, selection_session_id, room_member_id, participant_id, display_name, "
            "is_eligible, added_at) VALUES (%s, %s, %s, %s, %s, TRUE, %s)",
            (
                f"session-member-{index}-{suffix}",
                session_id,
                membership_ids[index],
                participant_id,
                "Ada" if index == 0 else "Grace",
                now,
            ),
        )
    database.execute(
        "INSERT INTO session_movies "
        "(id, selection_session_id, movie_id, position, source_feed, created_at) "
        "VALUES (%s, %s, %s, 0, 'combined', %s)",
        (f"session-movie-{suffix}", session_id, movie_id, now),
    )

    def like(participant_id):
        return record_swipe(
            database,
            room_id,
            session_id,
            participant_id,
            movie_id,
            "like",
            str(uuid.uuid4()),
            retries=5,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(like, participant_ids))

    matches = database.fetch_all(
        "SELECT id FROM matches WHERE selection_session_id = %s AND movie_id = %s",
        (session_id, movie_id),
    )
    assert len(matches) == 1
    assert sum(bool(result["matched"]) for result in results) >= 1
    members = database.fetch_all(
        "SELECT participant_id FROM match_members WHERE match_id = %s",
        (matches[0]["id"],),
    )
    assert {row["participant_id"] for row in members} == set(participant_ids)

    # RustyDB's prepared-result protocol used to expose INTEGER bytes using
    # string metadata. 0x89 is not valid as the first byte of UTF-8 and
    # reproduced the crash seen when opening a newly created match.
    database.execute(
        "UPDATE matches SET matched_at = %s WHERE id = %s",
        (0x89, matches[0]["id"]),
    )
    details = match_details(database, matches[0]["id"], room_id)
    assert details["matched_at"] == 0x89
    assert {row["display_name"] for row in details["members"]} == {"Ada", "Grace"}
