from pathlib import Path
import uuid

import pytest

from app import create_app
from app.database.migrations import apply_migrations
from app.identity.services import save_identity
from app.matching.services import (
    next_movie,
    record_swipe,
    session_progress,
    session_results,
    undo_last_swipe,
    vote_for_final_match,
)
from app.plex.sync import sync_movies
from app.rooms.services import (
    add_lobby_suggestion,
    cleanup_expired_sessions,
    create_lobby,
    create_room,
    delete_session,
    join_room,
    launch_lobby,
    search_lobby_movies,
    set_lobby_ready,
    start_session,
)

pytestmark = pytest.mark.integration


def test_complete_two_person_matcharr_workflow(database):
    apply_migrations(database, Path(__file__).resolve().parents[2] / "migrations")
    suffix = uuid.uuid4().hex[:8]
    owner_id = save_identity(database, None, f"Ada {suffix}")
    member_id = save_identity(database, None, f"Grace {suffix}")

    room = create_room(
        database,
        owner_id,
        f"Ada {suffix}",
        f"Friday films {suffix}",
        include_watched=False,
    )
    joined = join_room(
        database,
        member_id,
        f"Grace {suffix}",
        room["invite_code"].lower(),
    )
    assert joined["id"] == room["id"]

    class Plex:
        @staticmethod
        def movies():
            return [{
                "plex_rating_key": f"plex-workflow-{suffix}",
                "title": "Arrival",
                "release_year": 2016,
                "summary": "A linguist tries to understand visitors.",
                "poster_path": "/library/metadata/42/thumb",
                "background_path": "/library/metadata/42/art",
                "duration_minutes": 116,
                "content_rating": "PG-13",
                "audience_rating": 8.1,
                "critic_rating": 7.9,
                "genres": ["Drama", "Science Fiction"],
                "date_added": 1_700_000_000,
                "view_count": 0,
                "watched": False,
                "plex_watch_url": "plex://play/arrival",
                "metadata": {},
            }]

    synced = sync_movies(database, Plex())
    assert synced["movies_created"] == 1
    session_id = start_session(
        database,
        room["id"],
        owner_id,
        {"feed": "combined", "candidate_pool_size": 1},
    )
    owner_movie = next_movie(database, room["id"], session_id, owner_id)
    refreshed_movie = next_movie(database, room["id"], session_id, owner_id)
    member_movie = next_movie(database, room["id"], session_id, member_id)
    assert refreshed_movie["id"] == owner_movie["id"]
    assert owner_movie["id"] == member_movie["id"]

    first = record_swipe(
        database,
        room["id"],
        session_id,
        owner_id,
        owner_movie["id"],
        "like",
        str(uuid.uuid4()),
    )
    assert first["matched"] is False
    final = record_swipe(
        database,
        room["id"],
        session_id,
        member_id,
        member_movie["id"],
        "like",
        str(uuid.uuid4()),
    )
    assert final["matched"] is True
    assert final["session_completed"] is True
    completed = database.fetch_one(
        "SELECT status FROM selection_sessions WHERE id = %s", (session_id,)
    )
    assert completed["status"] == "completed"

    app = create_app(
        {"TESTING": True, "SECRET_KEY": "integration-secret"},
        database=database,
    )
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["participant_id"] = owner_id
        browser_session["display_name"] = f"Ada {suffix}"
    status_response = client.get(
        f"/api/rooms/{room['id']}/sessions/{session_id}/status"
    )
    assert status_response.status_code == 200
    status_payload = status_response.get_json()
    assert status_payload["session"]["status"] == "completed"
    assert status_payload["matches"][0]["title"] == "Arrival"
    assert status_payload["matches"][0]["poster_url"].startswith("/media/poster/")

    results_response = client.get(
        f"/rooms/{room['id']}/matches?session_id={session_id}"
    )
    assert results_response.status_code == 200
    assert b"Your final matches" in results_response.data


def test_room_actions_restore_participants_from_stale_browser_sessions(database):
    apply_migrations(database, Path(__file__).resolve().parents[2] / "migrations")
    suffix = uuid.uuid4().hex[:8]
    stale_owner_id = str(uuid.uuid4())
    stale_member_id = str(uuid.uuid4())

    room = create_room(
        database,
        stale_owner_id,
        f"Owner {suffix}",
        f"Restored room {suffix}",
        include_watched=False,
    )
    joined = join_room(
        database,
        stale_member_id,
        f"Member {suffix}",
        room["invite_code"],
    )

    assert joined["id"] == room["id"]
    assert database.fetch_one(
        "SELECT id FROM participants WHERE id = %s", (stale_owner_id,)
    )
    assert database.fetch_one(
        "SELECT id FROM participants WHERE id = %s", (stale_member_id,)
    )


def test_quick_join_link_creates_identity_and_joins_room(database):
    apply_migrations(database, Path(__file__).resolve().parents[2] / "migrations")
    suffix = uuid.uuid4().hex[:8]
    owner_name = f"Owner {suffix}"
    owner_id = save_identity(database, None, owner_name)
    room = create_room(
        database,
        owner_id,
        owner_name,
        f"Quick join {suffix}",
        include_watched=False,
    )
    app = create_app(
        {"TESTING": True, "SECRET_KEY": "integration-secret"},
        database=database,
    )
    client = app.test_client()

    response = client.get(f"/join/{room['invite_code'].lower()}")
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        token = browser_session["csrf_token"]
    display_name = f"Guest {suffix}"
    response = client.post(
        f"/join/{room['invite_code'].lower()}",
        data={"csrf_token": token, "display_name": display_name},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/rooms/{room['id']}")
    with client.session_transaction() as browser_session:
        participant_id = browser_session["participant_id"]
    membership = database.fetch_one(
        "SELECT display_name, is_active FROM room_members "
        "WHERE room_id = %s AND participant_id = %s",
        (room["id"], participant_id),
    )
    assert membership == {"display_name": display_name, "is_active": 1}


def test_lobby_bonus_undo_final_vote_and_session_deletion(database):
    apply_migrations(database, Path(__file__).resolve().parents[2] / "migrations")
    suffix = uuid.uuid4().hex[:8]
    owner_id = save_identity(database, None, f"Owner {suffix}")
    member_id = save_identity(database, None, f"Member {suffix}")
    room = create_room(database, owner_id, f"Owner {suffix}", f"Lobby {suffix}", False)
    join_room(database, member_id, f"Member {suffix}", room["invite_code"])

    class Plex:
        @staticmethod
        def movies():
            return [
                {
                    "plex_rating_key": f"base-{suffix}", "title": "Drama Pick",
                    "release_year": 2020, "summary": "", "poster_path": "/base",
                    "background_path": None, "duration_minutes": 100,
                    "content_rating": "PG", "audience_rating": 7.5,
                    "critic_rating": 7.0, "genres": ["Drama"], "date_added": 10,
                    "view_count": 0, "watched": False, "plex_watch_url": None,
                    "metadata": {},
                },
                {
                    "plex_rating_key": f"bonus-{suffix}", "title": f"Grown Ups 2 {suffix}",
                    "release_year": 2013, "summary": "", "poster_path": "/bonus",
                    "background_path": None, "duration_minutes": 101,
                    "content_rating": "PG-13", "audience_rating": 8.5,
                    "critic_rating": 6.0, "genres": ["Comedy"], "date_added": 9,
                    "view_count": 0, "watched": False, "plex_watch_url": None,
                    "metadata": {},
                },
            ]

    sync_movies(database, Plex())
    lobby_id = create_lobby(
        database,
        room["id"],
        owner_id,
        {"genres": ["Drama"], "candidate_pool_size": 1, "feed": "combined"},
    )
    suggestions = search_lobby_movies(
        database, room["id"], lobby_id, member_id, suffix
    )
    assert [movie["title"] for movie in suggestions] == [f"Grown Ups 2 {suffix}"]
    add_lobby_suggestion(
        database, room["id"], lobby_id, member_id, suggestions[0]["id"]
    )
    assert set_lobby_ready(database, room["id"], lobby_id, owner_id)["all_ready"] is False
    assert set_lobby_ready(database, room["id"], lobby_id, member_id)["all_ready"] is True
    session_id = launch_lobby(database, room["id"], lobby_id, owner_id)
    pool = database.fetch_all(
        "SELECT movie_id FROM session_movies WHERE selection_session_id = %s "
        "ORDER BY position ASC",
        (session_id,),
    )
    assert len(pool) == 2
    assert suggestions[0]["id"] in {row["movie_id"] for row in pool}

    first_movie = next_movie(database, room["id"], session_id, owner_id)
    record_swipe(
        database, room["id"], session_id, owner_id, first_movie["id"], "skip",
        str(uuid.uuid4()),
    )
    undone = undo_last_swipe(database, room["id"], session_id, owner_id)
    assert undone["movie_id"] == first_movie["id"]
    assert next_movie(database, room["id"], session_id, owner_id)["id"] == first_movie["id"]

    for movie in pool:
        for participant_id in (owner_id, member_id):
            record_swipe(
                database, room["id"], session_id, participant_id,
                movie["movie_id"], "like", str(uuid.uuid4()),
            )
    progress = session_progress(database, room["id"], session_id, owner_id)
    assert all(member["decided"] == 2 for member in progress["members"])
    results = session_results(database, room["id"], session_id, owner_id)
    assert len(results["matches"]) == 2
    winner_id = results["matches"][0]["id"]
    vote_for_final_match(database, room["id"], session_id, owner_id, winner_id)
    vote_for_final_match(database, room["id"], session_id, member_id, winner_id)
    assert session_results(database, room["id"], session_id, owner_id)["winner"]["id"] == winner_id

    delete_session(database, room["id"], session_id, owner_id)
    assert not database.fetch_one(
        "SELECT id FROM selection_sessions WHERE id = %s", (session_id,)
    )

    expired_id = start_session(
        database, room["id"], owner_id, {"candidate_pool_size": 1}
    )
    database.execute(
        "UPDATE selection_sessions SET started_at = %s WHERE id = %s",
        (1, expired_id),
    )
    assert cleanup_expired_sessions(database) >= 1
    assert not database.fetch_one(
        "SELECT id FROM selection_sessions WHERE id = %s", (expired_id,)
    )
