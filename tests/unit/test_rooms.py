import json
import random

import pytest

from app.rooms.services import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    _filter_movies,
    _weighted_movie_pool,
    archive_room,
    create_room,
    join_room,
    start_session,
    update_room_settings,
)
from app.rooms.routes import _filters_from_form


def test_create_room_generates_owner_membership(scripted_db, scripted_connection):
    result = create_room(scripted_db, "p1", "Ada", "Friday films", False)
    assert result["id"]
    assert len(result["invite_code"]) == 8
    statements = [query for query, _ in scripted_connection.executed]
    participant_insert = next(
        index for index, query in enumerate(statements)
        if query.startswith("INSERT INTO participants")
    )
    room_insert = next(
        index for index, query in enumerate(statements)
        if query.startswith("INSERT INTO rooms")
    )
    assert participant_insert < room_insert
    assert any(query.startswith("INSERT INTO rooms") for query in statements)
    assert any("'owner'" in query for query in statements if query.startswith("INSERT INTO room_members"))


def test_join_valid_room_and_reject_invalid_invite(scripted_db, scripted_connection):
    scripted_connection.one_handler = lambda query, params: (
        {"id": "room1", "name": "Films", "is_archived": False}
        if "FROM rooms WHERE invite_code" in query
        else None
    )
    result = join_room(scripted_db, "p2", "Grace", "abc23456")
    assert result["id"] == "room1"
    assert any(query.startswith("INSERT INTO room_members") for query, _ in scripted_connection.executed)

    scripted_connection.one_handler = lambda query, params: None
    with pytest.raises(NotFoundError):
        join_room(scripted_db, "p2", "Grace", "bad-code")


def test_duplicate_active_display_name_is_rejected(scripted_db, scripted_connection):
    scripted_connection.one_handler = lambda query, params: (
        {"id": "room1", "name": "Films", "is_archived": False}
        if "FROM rooms WHERE invite_code" in query
        else None
    )
    scripted_connection.all_handler = lambda query, params: [
        {
            "id": "membership1",
            "participant_id": "someone-else",
            "display_name": "GRACE",
            "is_active": True,
        }
    ]
    with pytest.raises(ConflictError):
        join_room(scripted_db, "p2", "Grace", "ABC23456")


def test_owner_only_archive_is_enforced(scripted_db, scripted_connection):
    scripted_connection.one_handler = lambda query, params: {"owner_participant_id": "p1"}
    with pytest.raises(AuthorizationError):
        archive_room(scripted_db, "room1", "p2")


def test_owner_can_rename_room_and_store_future_defaults(scripted_db, scripted_connection):
    scripted_connection.one_handler = lambda query, params: {"owner_participant_id": "p1"}
    update_room_settings(
        scripted_db,
        "room1",
        "p1",
        "Saturday films",
        True,
        {"feed": "popular", "candidate_pool_size": 25},
    )
    update = next(
        params
        for query, params in scripted_connection.executed
        if query.startswith("UPDATE rooms SET name")
    )
    assert update[0] == "Saturday films"
    assert json.loads(update[1])["feed"] == "popular"
    assert "include_watched = TRUE" in next(
        query
        for query, _ in scripted_connection.executed
        if query.startswith("UPDATE rooms SET name")
    )


def test_session_freezes_current_members_and_candidate_pool(scripted_db, scripted_connection):
    def one(query, params):
        if "SELECT owner_participant_id, include_watched" in query:
            return {"owner_participant_id": "p1", "include_watched": False}
        if "status = 'active'" in query:
            return None
        return None

    members = [
        {"id": "rm1", "participant_id": "p1", "display_name": "Ada"},
        {"id": "rm2", "participant_id": "p2", "display_name": "Grace"},
    ]
    movies = [
        {
            "id": "m1",
            "watched": False,
            "genres_json": '["Drama"]',
            "date_added": 1,
            "view_count": 0,
            "audience_rating": 8.0,
        }
    ]
    scripted_connection.one_handler = one
    scripted_connection.all_handler = lambda query, params: (
        members if "FROM room_members" in query else movies if query == "SELECT * FROM movies" else []
    )
    session_id = start_session(
        scripted_db, "room1", "p1", {"candidate_pool_size": 20}
    )
    assert session_id
    member_inserts = [
        params
        for query, params in scripted_connection.executed
        if query.startswith("INSERT INTO session_members")
    ]
    assert len(member_inserts) == 2
    assert {params[3] for params in member_inserts} == {"p1", "p2"}
    assert "late-joiner" not in {params[3] for params in member_inserts}


def test_discovery_filters_and_combined_order():
    movies = [
        {"id": "watched", "watched": True, "genres_json": '["Drama"]', "date_added": 1, "view_count": 9, "audience_rating": 9.0},
        {"id": "popular", "watched": False, "genres_json": '["Drama"]', "date_added": 1, "view_count": 5, "audience_rating": 7.0},
        {"id": "rated", "watched": False, "genres_json": '["Drama"]', "date_added": 1, "view_count": 0, "audience_rating": 8.2},
        {"id": "excluded", "watched": False, "genres_json": '["Horror"]', "date_added": 1, "view_count": 0, "audience_rating": 9.0},
    ]
    result = _filter_movies(movies, {"genres": ["Drama"]}, include_watched=False)
    assert [movie["id"] for movie in result] == ["popular", "rated"]


def test_weighted_pool_uses_quarter_added_quarter_released_half_random():
    movies = [
        {
            "id": f"added-{index}",
            "date_added": 2_000 - index,
            "release_year": None,
            "metadata_json": "{}",
        }
        for index in range(10)
    ] + [
        {
            "id": f"released-{index}",
            "date_added": None,
            "release_year": 2_030 - index,
            "metadata_json": "{}",
        }
        for index in range(10)
    ] + [
        {
            "id": f"random-{index}",
            "date_added": None,
            "release_year": None,
            "metadata_json": "{}",
        }
        for index in range(20)
    ]

    result = _weighted_movie_pool(
        movies,
        20,
        {
            "mix_recently_added": 25,
            "mix_recently_released": 25,
            "mix_random": 50,
        },
        random.Random(7),
    )

    assert len(result) == 20
    selected_ids = {movie["id"] for movie in result}
    assert len(selected_ids) == 20
    assert {f"added-{index}" for index in range(5)} <= selected_ids
    assert {f"released-{index}" for index in range(5)} <= selected_ids
    assert len(
        selected_ids
        - {f"added-{index}" for index in range(5)}
        - {f"released-{index}" for index in range(5)}
    ) == 10


def test_weighted_pool_normalizes_slider_values_and_rejects_all_zero():
    movies = [
        {
            "id": f"movie-{index}",
            "date_added": index + 1,
            "release_year": 2_000 + index,
            "metadata_json": "{}",
        }
        for index in range(20)
    ]
    result = _weighted_movie_pool(
        movies,
        10,
        {
            "mix_recently_added": 10,
            "mix_recently_released": 10,
            "mix_random": 80,
        },
        random.Random(3),
    )
    assert len(result) == 10
    with pytest.raises(ValueError, match="At least one"):
        _weighted_movie_pool(
            movies,
            10,
            {
                "mix_recently_added": 0,
                "mix_recently_released": 0,
                "mix_random": 0,
            },
        )


def test_movie_filters_keep_only_selected_media_server():
    movies = [
        {
            "id": "plex",
            "plex_rating_key": "42",
            "metadata_json": '{"source":"plex"}',
            "genres_json": "[]",
            "watched": False,
        },
        {
            "id": "jellyfin",
            "plex_rating_key": "jellyfin:42",
            "metadata_json": '{"source":"jellyfin"}',
            "genres_json": "[]",
            "watched": False,
        },
    ]
    result = _filter_movies(
        movies,
        {"media_source": "jellyfin"},
        include_watched=False,
    )
    assert [movie["id"] for movie in result] == ["jellyfin"]


def test_slider_form_values_and_selected_media_source_are_preserved(app):
    app.config["MEDIA_SERVER_TYPE"] = "jellyfin"
    with app.test_request_context(
        method="POST",
        data={
            "feed": "mixed",
            "candidate_pool_size": "80",
            "mix_recently_added": "20",
            "mix_recently_released": "35",
            "mix_random": "45",
        },
    ):
        filters = _filters_from_form()
    assert filters["feed"] == "mixed"
    assert filters["candidate_pool_size"] == 80
    assert filters["mix_recently_added"] == 20
    assert filters["mix_recently_released"] == 35
    assert filters["mix_random"] == 45
    assert filters["media_source"] == "jellyfin"

    with app.test_request_context(
        method="POST",
        data={
            "feed": "mixed",
            "mix_recently_added": "0",
            "mix_recently_released": "0",
            "mix_random": "0",
        },
    ):
        with pytest.raises(ValueError, match="At least one"):
            _filters_from_form()
