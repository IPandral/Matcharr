from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request, session

from app.matching.services import (
    next_movie,
    record_swipe,
    session_results,
    session_matches,
    session_progress,
    undo_last_swipe,
    vote_for_final_match,
    vote_to_finish,
)

from .services import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    add_lobby_suggestion,
    room_details,
    search_lobby_movies,
    set_lobby_ready,
)

bp = Blueprint("rooms_api", __name__, url_prefix="/api")


def _db():
    return current_app.extensions["matcharr_db"]


def _participant_id():
    return session.get("participant_id")


def _error(message, status):
    return jsonify(success=False, error=message), status


@bp.get("/rooms/<room_id>/status")
def status(room_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        details = room_details(_db(), room_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    active = details["active_session"]
    matches = []
    progress = None
    if active:
        matches = session_matches(_db(), room_id, active["id"], _participant_id())
        try:
            progress = session_progress(
                _db(), room_id, active["id"], _participant_id()
            )
        except AuthorizationError:
            progress = None
    return jsonify(
        room_id=room_id,
        session=active,
        members=[
            {"display_name": row["display_name"], "role": row["role"], "active": bool(row["is_active"])}
            for row in details["members"]
        ],
        matches=matches,
        progress=progress,
        lobby=details["lobby"],
        lobby_members=details["lobby_members"],
        suggestions=details["suggestions"],
    )


@bp.get("/rooms/<room_id>/members")
def members(room_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        details = room_details(_db(), room_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(
        members=[
            {"display_name": row["display_name"], "role": row["role"], "active": bool(row["is_active"])}
            for row in details["members"]
        ]
    )


def _movie_payload(movie):
    return {
        "id": movie["id"],
        "title": movie["title"],
        "release_year": movie.get("release_year"),
        "summary": movie.get("summary") or "",
        "poster_url": f"/media/poster/{movie['id']}" if movie.get("poster_path") else None,
        "background_url": f"/media/background/{movie['id']}" if movie.get("background_path") else None,
        "duration_minutes": movie.get("duration_minutes"),
        "content_rating": movie.get("content_rating"),
        "audience_rating": movie.get("audience_rating"),
        "critic_rating": movie.get("critic_rating"),
        "genres": json.loads(movie.get("genres_json") or "[]"),
    }


def _match_payload(match, room_id):
    return {
        **match,
        "poster_url": f"/media/poster/{match['movie_id']}",
        "url": f"/rooms/{room_id}/matches/{match['id']}",
    }


@bp.get("/rooms/<room_id>/sessions/<session_id>/status")
def session_status_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        details = room_details(_db(), room_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    session_row = _db().fetch_one(
        "SELECT id, status, started_at, completed_at FROM selection_sessions "
        "WHERE id = %s AND room_id = %s",
        (session_id, room_id),
    )
    if not session_row:
        return _error("Session not found.", 404)
    try:
        progress = session_progress(
            _db(), room_id, session_id, _participant_id()
        )
        matches = session_matches(
            _db(), room_id, session_id, _participant_id()
        )
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(
        session=session_row,
        progress=progress,
        matches=[_match_payload(match, room_id) for match in matches],
        members=[
            {
                "display_name": row["display_name"],
                "role": row["role"],
                "active": bool(row["is_active"]),
            }
            for row in details["members"]
        ],
    )


@bp.get("/rooms/<room_id>/sessions/<session_id>/next-movie")
def next_movie_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        movie = next_movie(_db(), room_id, session_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(
        movie=_movie_payload(movie) if movie else None,
        progress=session_progress(_db(), room_id, session_id, _participant_id()),
    )


@bp.post("/rooms/<room_id>/sessions/<session_id>/swipes")
def swipe_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("A JSON object is required.", 400)
    movie_id = payload.get("movie_id")
    decision = payload.get("decision")
    idempotency_key = payload.get("idempotency_key")
    if not all(isinstance(value, str) for value in (movie_id, decision, idempotency_key)):
        return _error("movie_id, decision, and idempotency_key are required.", 400)
    try:
        result = record_swipe(
            _db(),
            room_id,
            session_id,
            _participant_id(),
            movie_id,
            decision,
            idempotency_key,
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
        following = (
            None
            if result["session_completed"]
            else next_movie(_db(), room_id, session_id, _participant_id())
        )
    except ValueError as error:
        return _error(str(error), 400)
    except AuthorizationError as error:
        return _error(str(error), 403)
    except NotFoundError as error:
        return _error(str(error), 404)
    except ConflictError as error:
        return _error(str(error), 409)
    response = {
        "success": True,
        "matched": result["matched"],
        "session_completed": result["session_completed"],
        "next_movie_available": following is not None,
        "progress": session_progress(
            _db(), room_id, session_id, _participant_id()
        ),
    }
    if result["matched"]:
        details = _db().fetch_one(
            "SELECT ma.id, ma.movie_id, m.title, m.release_year, m.plex_watch_url "
            "FROM matches ma INNER JOIN movies m ON ma.movie_id = m.id WHERE ma.id = %s",
            (result["match_id"],),
        )
        response["match"] = _match_payload(details, room_id)
    return jsonify(response)


@bp.post("/rooms/<room_id>/sessions/<session_id>/finish-vote")
def finish_vote_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        result = vote_to_finish(
            _db(),
            room_id,
            session_id,
            _participant_id(),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError as error:
        return _error(str(error), 403)
    except NotFoundError as error:
        return _error(str(error), 404)
    return jsonify(success=True, **result)


@bp.post("/rooms/<room_id>/sessions/<session_id>/undo")
def undo_swipe_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        undone = undo_last_swipe(
            _db(),
            room_id,
            session_id,
            _participant_id(),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
        progress = session_progress(_db(), room_id, session_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    except NotFoundError as error:
        return _error(str(error), 404)
    except ConflictError as error:
        return _error(str(error), 409)
    return jsonify(success=True, undone=undone, progress=progress)


@bp.get("/rooms/<room_id>/lobbies/<lobby_id>/movie-search")
def lobby_movie_search_api(room_id, lobby_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        movies = search_lobby_movies(
            _db(),
            room_id,
            lobby_id,
            _participant_id(),
            request.args.get("q", ""),
            current_app.config["MEDIA_SERVER_TYPE"],
        )
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(movies=movies)


@bp.post("/rooms/<room_id>/lobbies/<lobby_id>/suggestions")
def lobby_suggestion_api(room_id, lobby_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    payload = request.get_json(silent=True) or {}
    movie_id = payload.get("movie_id")
    if not isinstance(movie_id, str):
        return _error("movie_id is required.", 400)
    try:
        add_lobby_suggestion(
            _db(),
            room_id,
            lobby_id,
            _participant_id(),
            movie_id,
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
            current_app.config["MEDIA_SERVER_TYPE"],
        )
    except AuthorizationError as error:
        return _error(str(error), 403)
    except NotFoundError as error:
        return _error(str(error), 404)
    return jsonify(success=True)


@bp.post("/rooms/<room_id>/lobbies/<lobby_id>/ready")
def lobby_ready_api(room_id, lobby_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        result = set_lobby_ready(
            _db(),
            room_id,
            lobby_id,
            _participant_id(),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(success=True, **result)


@bp.get("/rooms/<room_id>/sessions/<session_id>/results")
def session_results_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        result = session_results(_db(), room_id, session_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(**result)


@bp.post("/rooms/<room_id>/sessions/<session_id>/final-vote")
def final_vote_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    payload = request.get_json(silent=True) or {}
    match_id = payload.get("match_id")
    if not isinstance(match_id, str):
        return _error("match_id is required.", 400)
    try:
        vote_for_final_match(
            _db(),
            room_id,
            session_id,
            _participant_id(),
            match_id,
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
        result = session_results(_db(), room_id, session_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    except NotFoundError as error:
        return _error(str(error), 404)
    return jsonify(success=True, **result)


@bp.get("/rooms/<room_id>/sessions/<session_id>/matches")
def matches_api(room_id, session_id):
    if not _participant_id():
        return _error("Identity required.", 401)
    try:
        matches = session_matches(_db(), room_id, session_id, _participant_id())
    except AuthorizationError as error:
        return _error(str(error), 403)
    return jsonify(matches=[_match_payload(match, room_id) for match in matches])
