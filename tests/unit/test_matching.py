import uuid

import pytest

from app.matching.services import next_movie, record_swipe, vote_to_finish
from app.rooms.services import AuthorizationError, ConflictError


def _matching_handlers(
    scripted_connection,
    likes=1,
    skips=0,
    decisions=1,
    pool_size=1,
    eligible_members=2,
    prior_request=None,
    prior_swipe=None,
):
    def one(query, params):
        if "FROM selection_sessions" in query:
            return {"id": "s1", "status": "active"}
        if "FROM session_members" in query and "COUNT" not in query:
            return {"id": "sm1"}
        if "COUNT(*) AS total FROM session_movies" in query:
            return {"total": pool_size}
        if "FROM session_movies" in query:
            return {"id": "candidate"}
        if "WHERE idempotency_key" in query:
            return prior_request
        if "SELECT decision FROM swipes" in query:
            return prior_swipe
        if "COUNT(*) AS total FROM session_members" in query:
            return {"total": eligible_members}
        if "s.decision = 'like'" in query:
            return {"total": likes}
        if "s.decision = 'skip'" in query:
            return {"total": skips}
        if "COUNT(*) AS total FROM swipes s" in query:
            return {"total": decisions}
        if "FROM matches" in query:
            return None
        return None

    scripted_connection.one_handler = one
    scripted_connection.all_handler = lambda query, params: [
        {"participant_id": "p1", "display_name": "Ada"},
        {"participant_id": "p2", "display_name": "Grace"},
    ] if "FROM session_members sm INNER JOIN swipes" in query else []


def test_next_movie_requires_frozen_eligibility(scripted_db, scripted_connection):
    def one(query, params):
        if "FROM session_members sm" in query:
            return {"id": "eligible"}
        if "SELECT movie_id FROM session_movies" in query:
            return {"movie_id": "m1"}
        return {"id": "m1", "title": "Arrival", "genres_json": "[]"}

    scripted_connection.one_handler = one
    assert next_movie(scripted_db, "room1", "s1", "p1")["title"] == "Arrival"
    scripted_connection.one_handler = lambda query, params: None
    with pytest.raises(AuthorizationError):
        next_movie(scripted_db, "room1", "s1", "late-joiner")


def test_saved_current_movie_is_returned_again_after_refresh(
    scripted_db, scripted_connection
):
    def one(query, params):
        if "FROM session_members sm" in query:
            return {"id": "eligible"}
        if "FROM session_member_state" in query:
            return {"id": "state1", "current_movie_id": "m1"}
        if "FROM session_movies" in query:
            return {"id": "pool1"}
        if "FROM swipes" in query:
            return None
        if "FROM movies" in query:
            return {"id": "m1", "title": "Arrival", "genres_json": "[]"}
        return None

    scripted_connection.one_handler = one
    movie = next_movie(scripted_db, "room1", "s1", "p1")
    assert movie["id"] == "m1"
    assert not any(
        query.startswith("UPDATE session_member_state")
        for query, _ in scripted_connection.executed
    )


def test_like_records_without_matching_until_everyone_likes(scripted_db, scripted_connection):
    _matching_handlers(scripted_connection, likes=1)
    result = record_swipe(
        scripted_db, "room1", "s1", "p1", "m1", "like", str(uuid.uuid4())
    )
    assert result["matched"] is False
    assert not any(query.startswith("INSERT INTO matches") for query, _ in scripted_connection.executed)


def test_unanimous_like_creates_one_match_and_member_snapshot(scripted_db, scripted_connection):
    _matching_handlers(scripted_connection, likes=2)
    result = record_swipe(
        scripted_db, "room1", "s1", "p2", "m1", "like", str(uuid.uuid4())
    )
    assert result["matched"] is True
    assert sum(query.startswith("INSERT INTO matches") for query, _ in scripted_connection.executed) == 1
    assert sum(query.startswith("INSERT INTO match_members") for query, _ in scripted_connection.executed) == 2


def test_last_decision_completes_the_session(scripted_db, scripted_connection):
    _matching_handlers(scripted_connection, decisions=4, pool_size=2, eligible_members=2)
    result = record_swipe(
        scripted_db, "room1", "s1", "p2", "m1", "skip", str(uuid.uuid4())
    )
    assert result["session_completed"] is True
    assert any(
        query.startswith("UPDATE selection_sessions SET status = 'completed'")
        for query, _ in scripted_connection.executed
    )


def test_late_joiner_is_not_counted_outside_frozen_session_members(
    scripted_db, scripted_connection
):
    _matching_handlers(scripted_connection, likes=2)
    result = record_swipe(
        scripted_db, "room1", "s1", "p2", "m1", "like", str(uuid.uuid4())
    )
    assert result["matched"] is True
    eligible_count_query = next(
        query
        for query, _ in scripted_connection.queries
        if "COUNT(*) AS total FROM session_members" in query
    )
    assert "room_members" not in eligible_count_query


def test_skip_never_matches(scripted_db, scripted_connection):
    _matching_handlers(scripted_connection, likes=2, skips=1)
    result = record_swipe(
        scripted_db, "room1", "s1", "p2", "m1", "skip", str(uuid.uuid4())
    )
    assert result["matched"] is False


def test_duplicate_swipe_is_rejected(scripted_db, scripted_connection):
    _matching_handlers(scripted_connection, prior_swipe={"decision": "like"})
    with pytest.raises(ConflictError):
        record_swipe(
            scripted_db, "room1", "s1", "p1", "m1", "like", str(uuid.uuid4())
        )


def test_repeated_idempotency_key_returns_original_result(scripted_db, scripted_connection):
    _matching_handlers(
        scripted_connection,
        prior_request={"movie_id": "m1", "decision": "like"},
    )
    result = record_swipe(
        scripted_db, "room1", "s1", "p1", "m1", "like", str(uuid.uuid4())
    )
    assert result["replayed"] is True
    assert not any(query.startswith("INSERT INTO swipes") for query, _ in scripted_connection.executed)


def test_every_eligible_member_must_vote_before_session_stops(
    scripted_db, scripted_connection
):
    scripted_connection.one_handler = lambda query, params: (
        {"id": "s1"} if "FROM selection_sessions" in query else None
    )
    scripted_connection.all_handler = lambda query, params: (
        [{"participant_id": "p1"}, {"participant_id": "p2"}]
        if "FROM session_members" in query
        else [{"participant_id": "p1"}, {"participant_id": "p2"}]
    )
    result = vote_to_finish(scripted_db, "room1", "s1", "p2")
    assert result == {
        "completed": True,
        "finish_votes": 2,
        "eligible_members": 2,
    }
    assert any(
        query.startswith("UPDATE selection_sessions SET status = 'completed'")
        for query, _ in scripted_connection.executed
    )
