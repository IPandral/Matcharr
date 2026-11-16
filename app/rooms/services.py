from __future__ import annotations

import json
import random
import secrets
import time
import uuid

from app.database.transaction import run_transaction
from app.identity.services import save_identity

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_NAME_MAX = 80


class AuthorizationError(PermissionError):
    pass


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


def _room_name(value: str | None) -> str:
    name = (value or "").strip()
    if not name or len(name) > ROOM_NAME_MAX:
        raise ValueError(f"Room names must be between 1 and {ROOM_NAME_MAX} characters.")
    return name


def _invite_code(length: int) -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(max(6, length)))


def create_room(database, participant_id: str, display_name: str, name: str, include_watched: bool,
                invite_length: int = 8, retries: int = 3) -> dict:
    name = _room_name(name)
    # Browser sessions can outlive a replaced/reset RustyDB volume. Restore the
    # signed-in participant before inserting rows that reference it.
    save_identity(database, participant_id, display_name, retries)

    def operation(connection):
        now = int(time.time())
        room_id = str(uuid.uuid4())
        membership_id = str(uuid.uuid4())
        for _ in range(10):
            code = _invite_code(invite_length)
            if not connection.fetch_one(
                "SELECT id FROM rooms WHERE invite_code = %s", (code,)
            ):
                break
        else:
            raise ConflictError("Could not generate a unique invite code.")
        watched_literal = "TRUE" if include_watched else "FALSE"
        connection.execute(
            "INSERT INTO rooms "
            "(id, owner_participant_id, name, invite_code, is_archived, include_watched, "
            "default_filters_json, created_at, updated_at) "
            f"VALUES (%s, %s, %s, %s, FALSE, {watched_literal}, %s, %s, %s)",
            (room_id, participant_id, name, code, "{}", now, now),
        )
        connection.execute(
            "INSERT INTO room_members "
            "(id, room_id, participant_id, display_name, role, is_active, joined_at, left_at) "
            "VALUES (%s, %s, %s, %s, 'owner', TRUE, %s, NULL)",
            (membership_id, room_id, participant_id, display_name, now),
        )
        return {"id": room_id, "invite_code": code}

    return run_transaction(database, operation, retries)


def join_room(database, participant_id: str, display_name: str, invite_code: str,
              retries: int = 3) -> dict:
    code = (invite_code or "").strip().upper()
    if not code:
        raise ValueError("Enter an invite code.")
    save_identity(database, participant_id, display_name, retries)

    def operation(connection):
        room = connection.fetch_one(
            "SELECT id, name, is_archived FROM rooms WHERE invite_code = %s", (code,)
        )
        if not room or room["is_archived"]:
            raise NotFoundError("That invite code is invalid or the room is archived.")
        members = connection.fetch_all(
            "SELECT id, participant_id, display_name, is_active FROM room_members "
            "WHERE room_id = %s",
            (room["id"],),
        )
        if any(
            member["is_active"]
            and member["participant_id"] != participant_id
            and member["display_name"].casefold() == display_name.casefold()
            for member in members
        ):
            raise ConflictError("That display name is already active in this room.")
        existing = next(
            (member for member in members if member["participant_id"] == participant_id), None
        )
        now = int(time.time())
        if existing:
            membership_id = existing["id"]
            connection.execute(
                "UPDATE room_members SET display_name = %s, is_active = TRUE, left_at = NULL "
                "WHERE id = %s",
                (display_name, existing["id"]),
            )
        else:
            membership_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO room_members "
                "(id, room_id, participant_id, display_name, role, is_active, joined_at, left_at) "
                "VALUES (%s, %s, %s, %s, 'member', TRUE, %s, NULL)",
                (membership_id, room["id"], participant_id, display_name, now),
            )
        lobby = connection.fetch_one(
            "SELECT id FROM room_lobbies WHERE room_id = %s", (room["id"],)
        )
        if lobby and not connection.fetch_one(
            "SELECT id FROM lobby_members WHERE lobby_id = %s AND participant_id = %s",
            (lobby["id"], participant_id),
        ):
            connection.execute(
                "INSERT INTO lobby_members "
                "(id, lobby_id, room_member_id, participant_id, display_name, is_ready, added_at) "
                "VALUES (%s, %s, %s, %s, %s, FALSE, %s)",
                (
                    str(uuid.uuid4()),
                    lobby["id"],
                    membership_id,
                    participant_id,
                    display_name,
                    now,
                ),
            )
        return room

    return run_transaction(database, operation, retries)


def get_membership(database, room_id: str, participant_id: str) -> dict:
    membership = database.fetch_one(
        "SELECT rm.id, rm.role, rm.is_active, rm.display_name, "
        "r.name AS room_name, r.invite_code, r.is_archived, r.include_watched, "
        "r.owner_participant_id, r.default_filters_json "
        "FROM room_members rm INNER JOIN rooms r ON rm.room_id = r.id "
        "WHERE rm.room_id = %s AND rm.participant_id = %s",
        (room_id, participant_id),
    )
    if not membership or not membership["is_active"]:
        raise AuthorizationError("You are not an active member of this room.")
    return membership


def list_rooms(database, participant_id: str) -> list[dict]:
    cleanup_expired_sessions(database)
    return database.fetch_all(
        "SELECT r.id, r.name, r.invite_code, r.is_archived, rm.role, rm.is_active "
        "FROM room_members rm INNER JOIN rooms r ON rm.room_id = r.id "
        "WHERE rm.participant_id = %s ORDER BY r.updated_at DESC",
        (participant_id,),
    )


def room_details(database, room_id: str, participant_id: str) -> dict:
    cleanup_expired_sessions(database)
    membership = get_membership(database, room_id, participant_id)
    members = database.fetch_all(
        "SELECT id, participant_id, display_name, role, is_active, joined_at "
        "FROM room_members WHERE room_id = %s ORDER BY joined_at ASC",
        (room_id,),
    )
    active_session = database.fetch_one(
        "SELECT id, status, filters_json, started_at FROM selection_sessions "
        "WHERE room_id = %s AND status = 'active' ORDER BY started_at DESC LIMIT 1",
        (room_id,),
    )
    lobby = database.fetch_one(
        "SELECT id, filters_json, created_at FROM room_lobbies WHERE room_id = %s",
        (room_id,),
    )
    lobby_members = []
    suggestions = []
    if lobby:
        lobby_members = database.fetch_all(
            "SELECT participant_id, display_name, is_ready FROM lobby_members "
            "WHERE lobby_id = %s ORDER BY added_at ASC",
            (lobby["id"],),
        )
        suggestions = database.fetch_all(
            "SELECT ls.id, ls.participant_id, m.id AS movie_id, m.title, m.release_year "
            "FROM lobby_suggestions ls INNER JOIN movies m ON ls.movie_id = m.id "
            "WHERE ls.lobby_id = %s ORDER BY ls.created_at ASC",
            (lobby["id"],),
        )
    recent_sessions = database.fetch_all(
        "SELECT id, status, started_at, completed_at FROM selection_sessions "
        "WHERE room_id = %s ORDER BY started_at DESC",
        (room_id,),
    )
    try:
        default_filters = json.loads(membership.get("default_filters_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        default_filters = {}
    return {
        "membership": membership,
        "members": members,
        "active_session": active_session,
        "lobby": lobby,
        "lobby_members": lobby_members,
        "suggestions": suggestions,
        "recent_sessions": recent_sessions,
        "default_filters": default_filters,
    }


def leave_room(database, room_id: str, participant_id: str, retries: int = 3) -> None:
    def operation(connection):
        membership = connection.fetch_one(
            "SELECT id, role FROM room_members WHERE room_id = %s AND participant_id = %s "
            "AND is_active = TRUE",
            (room_id, participant_id),
        )
        if not membership:
            raise AuthorizationError("You are not an active member of this room.")
        if membership["role"] == "owner":
            raise ConflictError("Archive the room instead of leaving it as owner.")
        connection.execute(
            "UPDATE room_members SET is_active = FALSE, left_at = %s WHERE id = %s",
            (int(time.time()), membership["id"]),
        )

    run_transaction(database, operation, retries)


def archive_room(database, room_id: str, participant_id: str, retries: int = 3) -> None:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s", (room_id,)
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can archive it.")
        now = int(time.time())
        lobby = connection.fetch_one(
            "SELECT id FROM room_lobbies WHERE room_id = %s", (room_id,)
        )
        if lobby:
            connection.execute(
                "DELETE FROM lobby_suggestions WHERE lobby_id = %s", (lobby["id"],)
            )
            connection.execute(
                "DELETE FROM lobby_members WHERE lobby_id = %s", (lobby["id"],)
            )
            connection.execute("DELETE FROM room_lobbies WHERE id = %s", (lobby["id"],))
        connection.execute(
            "UPDATE rooms SET is_archived = TRUE, updated_at = %s WHERE id = %s",
            (now, room_id),
        )
        connection.execute(
            "UPDATE room_members SET is_active = FALSE, left_at = %s "
            "WHERE room_id = %s AND is_active = TRUE",
            (now, room_id),
        )
        connection.execute(
            "UPDATE selection_sessions SET status = 'cancelled', completed_at = %s "
            "WHERE room_id = %s AND status = 'active'",
            (now, room_id),
        )

    run_transaction(database, operation, retries)


def update_room_settings(
    database,
    room_id: str,
    participant_id: str,
    name: str,
    include_watched: bool,
    filters: dict,
    retries: int = 3,
) -> None:
    cleaned_name = _room_name(name)
    filters_json = json.dumps(filters, separators=(",", ":"), sort_keys=True)

    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s AND is_archived = FALSE",
            (room_id,),
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can change room settings.")
        watched_literal = "TRUE" if include_watched else "FALSE"
        connection.execute(
            f"UPDATE rooms SET name = %s, include_watched = {watched_literal}, "
            "default_filters_json = %s, updated_at = %s WHERE id = %s",
            (cleaned_name, filters_json, int(time.time()), room_id),
        )

    run_transaction(database, operation, retries)


def remove_member(database, room_id: str, participant_id: str, member_id: str,
                  remove_from_session: bool, retries: int = 3) -> None:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s", (room_id,)
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can remove members.")
        member = connection.fetch_one(
            "SELECT participant_id, role FROM room_members WHERE id = %s AND room_id = %s",
            (member_id, room_id),
        )
        if not member or member["role"] == "owner":
            raise ConflictError("The owner cannot be removed.")
        now = int(time.time())
        connection.execute(
            "UPDATE room_members SET is_active = FALSE, left_at = %s WHERE id = %s",
            (now, member_id),
        )
        lobby = connection.fetch_one(
            "SELECT id FROM room_lobbies WHERE room_id = %s", (room_id,)
        )
        if lobby:
            connection.execute(
                "DELETE FROM lobby_suggestions WHERE lobby_id = %s AND participant_id = %s",
                (lobby["id"], member["participant_id"]),
            )
            connection.execute(
                "DELETE FROM lobby_members WHERE lobby_id = %s AND participant_id = %s",
                (lobby["id"], member["participant_id"]),
            )
        if remove_from_session:
            active = connection.fetch_one(
                "SELECT id FROM selection_sessions WHERE room_id = %s AND status = 'active' "
                "ORDER BY started_at DESC LIMIT 1",
                (room_id,),
            )
            if active:
                connection.execute(
                    "UPDATE session_members SET is_eligible = FALSE "
                    "WHERE selection_session_id = %s AND room_member_id = %s",
                    (active["id"], member_id),
                )

    run_transaction(database, operation, retries)


def _filter_movies(rows: list[dict], filters: dict, include_watched: bool) -> list[dict]:
    include_genres = {value.casefold() for value in filters.get("genres", [])}
    exclude_genres = {value.casefold() for value in filters.get("excluded_genres", [])}

    def allowed(movie):
        genres = {value.casefold() for value in json.loads(movie.get("genres_json") or "[]")}
        if filters.get("media_source") and _movie_source(movie) != filters["media_source"]:
            return False
        if not include_watched and movie.get("watched"):
            return False
        if include_genres and not (genres & include_genres):
            return False
        if exclude_genres & genres:
            return False
        if filters.get("minimum_audience_rating") is not None and (
            movie.get("audience_rating") is None
            or float(movie["audience_rating"]) < float(filters["minimum_audience_rating"])
        ):
            return False
        if filters.get("maximum_runtime") and (
            movie.get("duration_minutes") or 10**9
        ) > int(filters["maximum_runtime"]):
            return False
        if filters.get("earliest_release_year") and (
            movie.get("release_year") or 0
        ) < int(filters["earliest_release_year"]):
            return False
        if filters.get("latest_release_year") and (
            movie.get("release_year") or 10**9
        ) > int(filters["latest_release_year"]):
            return False
        return True

    candidates = [movie for movie in rows if allowed(movie)]
    now = int(time.time())
    if filters.get("recently_added_only"):
        candidates = [
            movie for movie in candidates
            if (movie.get("date_added") or 0) >= now - 60 * 86400
        ]
    if filters.get("popular_only"):
        candidates = [movie for movie in candidates if (movie.get("view_count") or 0) > 0]
    feed = filters.get("feed", "combined")
    if feed == "recent":
        candidates = sorted(candidates, key=lambda movie: -(movie.get("date_added") or 0))
    elif feed == "popular":
        candidates = sorted(candidates, key=lambda movie: -(movie.get("view_count") or 0))
    elif feed == "rated":
        candidates = sorted(candidates, key=lambda movie: -(movie.get("audience_rating") or 0))
    elif feed == "unwatched":
        candidates = [movie for movie in candidates if not movie.get("watched")]

    def tier(movie):
        recent = (movie.get("date_added") or 0) >= now - 60 * 86400
        watched = bool(movie.get("watched"))
        popular = (movie.get("view_count") or 0) > 0
        highly_rated = (movie.get("audience_rating") or 0) >= 7.5
        if recent and not watched:
            return (0, -(movie.get("date_added") or 0))
        if popular:
            return (1, -(movie.get("view_count") or 0))
        if highly_rated and not watched:
            return (2, -(movie.get("audience_rating") or 0))
        if not watched:
            return (3, -(movie.get("date_added") or 0))
        return (4, -(movie.get("date_added") or 0))

    return sorted(candidates, key=tier) if feed == "combined" else candidates


def _movie_source(movie: dict) -> str:
    try:
        metadata = json.loads(movie.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    if metadata.get("source") in {"plex", "jellyfin"}:
        return metadata["source"]
    return (
        "jellyfin"
        if str(movie.get("plex_rating_key") or "").startswith("jellyfin:")
        else "plex"
    )


def _weighted_movie_pool(
    candidates: list[dict],
    size: int,
    filters: dict,
    rng=None,
) -> list[dict]:
    size = min(max(int(size), 1), len(candidates))
    if not candidates:
        return []
    weights = {
        "recently_added": int(filters.get("mix_recently_added", 25)),
        "recently_released": int(filters.get("mix_recently_released", 25)),
        "random": int(filters.get("mix_random", 50)),
    }
    if any(value < 0 or value > 100 for value in weights.values()):
        raise ValueError("Movie mix sliders must be between 0 and 100.")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("At least one movie mix slider must be above zero.")

    exact = {key: size * value / total for key, value in weights.items()}
    quotas = {key: int(value) for key, value in exact.items()}
    remainder = size - sum(quotas.values())
    for key in sorted(exact, key=lambda item: exact[item] - quotas[item], reverse=True):
        if remainder <= 0:
            break
        quotas[key] += 1
        remainder -= 1

    randomizer = rng or random.SystemRandom()
    random_order = list(candidates)
    randomizer.shuffle(random_order)
    added_order = sorted(
        (movie for movie in candidates if movie.get("date_added")),
        key=lambda movie: movie["date_added"],
        reverse=True,
    )
    released_order = sorted(
        (movie for movie in candidates if movie.get("release_year")),
        key=lambda movie: (
            movie.get("release_year") or 0,
            _premiere_date(movie),
        ),
        reverse=True,
    )
    orders = {
        "recently_added": added_order,
        "recently_released": released_order,
        "random": random_order,
    }
    selected = []
    selected_ids = set()
    for bucket in ("recently_added", "recently_released", "random"):
        if quotas[bucket] <= 0:
            continue
        count = 0
        for movie in orders[bucket]:
            if movie["id"] in selected_ids:
                continue
            selected.append(movie)
            selected_ids.add(movie["id"])
            count += 1
            if count >= quotas[bucket]:
                break

    if len(selected) < size:
        for movie in random_order:
            if movie["id"] not in selected_ids:
                selected.append(movie)
                selected_ids.add(movie["id"])
                if len(selected) >= size:
                    break
    randomizer.shuffle(selected)
    return selected[:size]


def _premiere_date(movie: dict) -> str:
    try:
        metadata = json.loads(movie.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(
        metadata.get("premiere_date")
        or metadata.get("originally_available_at")
        or ""
    )


def start_session(
    database,
    room_id: str,
    participant_id: str,
    filters: dict,
    retries: int = 3,
    bonus_movie_ids: list[str] | None = None,
) -> str:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id, include_watched FROM rooms "
            "WHERE id = %s AND is_archived = FALSE",
            (room_id,),
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can start a session.")
        if connection.fetch_one(
            "SELECT id FROM selection_sessions WHERE room_id = %s AND status = 'active'",
            (room_id,),
        ):
            raise ConflictError("This room already has an active session.")
        members = connection.fetch_all(
            "SELECT id, participant_id, display_name FROM room_members "
            "WHERE room_id = %s AND is_active = TRUE ORDER BY joined_at ASC",
            (room_id,),
        )
        if not members:
            raise ConflictError("The room has no active members.")
        all_movies = connection.fetch_all("SELECT * FROM movies")
        chosen = _filter_movies(all_movies, filters, bool(room["include_watched"]))
        size = min(max(int(filters.get("candidate_pool_size", 50)), 1), 500)
        if filters.get("feed") == "mixed":
            chosen = _weighted_movie_pool(chosen, size, filters)
        else:
            chosen = chosen[:size]
        chosen_ids = {movie["id"] for movie in chosen}
        bonus_ids = set(bonus_movie_ids or [])
        for movie in all_movies:
            source_matches = (
                not filters.get("media_source")
                or _movie_source(movie) == filters["media_source"]
            )
            if (
                movie["id"] in bonus_ids
                and movie["id"] not in chosen_ids
                and source_matches
            ):
                chosen.append(movie)
                chosen_ids.add(movie["id"])
        if not chosen:
            raise ConflictError("No synced movies match these filters.")
        session_id = str(uuid.uuid4())
        now = int(time.time())
        filters_json = json.dumps(filters, separators=(",", ":"), sort_keys=True)
        connection.execute(
            "INSERT INTO selection_sessions "
            "(id, room_id, created_by_participant_id, status, filters_json, started_at, completed_at) "
            "VALUES (%s, %s, %s, 'active', %s, %s, NULL)",
            (session_id, room_id, participant_id, filters_json, now),
        )
        for member in members:
            connection.execute(
                "INSERT INTO session_members "
                "(id, selection_session_id, room_member_id, participant_id, display_name, "
                "is_eligible, added_at) VALUES (%s, %s, %s, %s, %s, TRUE, %s)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    member["id"],
                    member["participant_id"],
                    member["display_name"],
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO session_member_state "
                "(id, selection_session_id, participant_id, current_movie_id, updated_at) "
                "VALUES (%s, %s, %s, NULL, %s)",
                (str(uuid.uuid4()), session_id, member["participant_id"], now),
            )
        for position, movie in enumerate(chosen):
            connection.execute(
                "INSERT INTO session_movies "
                "(id, selection_session_id, movie_id, position, source_feed, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    movie["id"],
                    position,
                    filters.get("feed", "combined"),
                    now,
                ),
            )
        return session_id

    return run_transaction(database, operation, retries)


def finish_session(database, room_id: str, session_id: str, participant_id: str,
                   retries: int = 3) -> None:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s", (room_id,)
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can finish a session.")
        session_row = connection.fetch_one(
            "SELECT id FROM selection_sessions WHERE id = %s AND room_id = %s "
            "AND status = 'active'",
            (session_id, room_id),
        )
        if not session_row:
            raise NotFoundError("Active session not found.")
        connection.execute(
            "UPDATE selection_sessions SET status = 'completed', completed_at = %s WHERE id = %s",
            (int(time.time()), session_id),
        )

    run_transaction(database, operation, retries)


def create_lobby(
    database,
    room_id: str,
    participant_id: str,
    filters: dict,
    retries: int = 3,
) -> str:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s AND is_archived = FALSE",
            (room_id,),
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can create a lobby.")
        if connection.fetch_one(
            "SELECT id FROM selection_sessions WHERE room_id = %s AND status = 'active'",
            (room_id,),
        ):
            raise ConflictError("This room already has an active session.")
        if connection.fetch_one("SELECT id FROM room_lobbies WHERE room_id = %s", (room_id,)):
            raise ConflictError("This room already has a lobby.")
        members = connection.fetch_all(
            "SELECT id, participant_id, display_name FROM room_members "
            "WHERE room_id = %s AND is_active = TRUE ORDER BY joined_at ASC",
            (room_id,),
        )
        if not members:
            raise ConflictError("The room has no active members.")
        lobby_id = str(uuid.uuid4())
        now = int(time.time())
        connection.execute(
            "INSERT INTO room_lobbies "
            "(id, room_id, created_by_participant_id, filters_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                lobby_id,
                room_id,
                participant_id,
                json.dumps(filters, separators=(",", ":"), sort_keys=True),
                now,
                now,
            ),
        )
        for member in members:
            connection.execute(
                "INSERT INTO lobby_members "
                "(id, lobby_id, room_member_id, participant_id, display_name, is_ready, added_at) "
                "VALUES (%s, %s, %s, %s, %s, FALSE, %s)",
                (
                    str(uuid.uuid4()),
                    lobby_id,
                    member["id"],
                    member["participant_id"],
                    member["display_name"],
                    now,
                ),
            )
        return lobby_id

    return run_transaction(database, operation, retries)


def set_lobby_ready(
    database, room_id: str, lobby_id: str, participant_id: str, retries: int = 3
) -> dict:
    def operation(connection):
        member = connection.fetch_one(
            "SELECT lm.id FROM lobby_members lm INNER JOIN room_lobbies rl "
            "ON lm.lobby_id = rl.id WHERE lm.lobby_id = %s AND lm.participant_id = %s "
            "AND rl.room_id = %s",
            (lobby_id, participant_id, room_id),
        )
        if not member:
            raise AuthorizationError("You are not a member of this lobby.")
        connection.execute(
            "UPDATE lobby_members SET is_ready = TRUE WHERE id = %s",
            (member["id"],),
        )
        rows = connection.fetch_all(
            "SELECT participant_id, display_name, is_ready FROM lobby_members "
            "WHERE lobby_id = %s ORDER BY added_at ASC",
            (lobby_id,),
        )
        return {
            "all_ready": bool(rows) and all(bool(row["is_ready"]) for row in rows),
            "members": rows,
        }

    return run_transaction(database, operation, retries)


def search_lobby_movies(
    database,
    room_id: str,
    lobby_id: str,
    participant_id: str,
    query: str,
    media_source: str | None = None,
) -> list[dict]:
    term = (query or "").strip()
    if len(term) < 2:
        return []
    if not database.fetch_one(
        "SELECT lm.id FROM lobby_members lm INNER JOIN room_lobbies rl "
        "ON lm.lobby_id = rl.id WHERE lm.lobby_id = %s AND lm.participant_id = %s "
        "AND rl.room_id = %s",
        (lobby_id, participant_id, room_id),
    ):
        raise AuthorizationError("You are not a member of this lobby.")
    rows = database.fetch_all(
        "SELECT id, plex_rating_key, title, release_year, metadata_json "
        "FROM movies WHERE title LIKE %s "
        "ORDER BY title ASC, release_year ASC LIMIT 100",
        (f"%{term}%",),
    )
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "release_year": row.get("release_year"),
        }
        for row in rows
        if not media_source or _movie_source(row) == media_source
    ][:12]


def add_lobby_suggestion(
    database,
    room_id: str,
    lobby_id: str,
    participant_id: str,
    movie_id: str,
    retries: int = 3,
    media_source: str | None = None,
) -> None:
    def operation(connection):
        member = connection.fetch_one(
            "SELECT lm.id FROM lobby_members lm INNER JOIN room_lobbies rl "
            "ON lm.lobby_id = rl.id WHERE lm.lobby_id = %s AND lm.participant_id = %s "
            "AND rl.room_id = %s",
            (lobby_id, participant_id, room_id),
        )
        if not member:
            raise AuthorizationError("You are not a member of this lobby.")
        movie = connection.fetch_one(
            "SELECT id, plex_rating_key, metadata_json FROM movies WHERE id = %s",
            (movie_id,),
        )
        if not movie or (media_source and _movie_source(movie) != media_source):
            raise NotFoundError("That movie is no longer available.")
        if not connection.fetch_one(
            "SELECT id FROM lobby_suggestions WHERE lobby_id = %s "
            "AND participant_id = %s AND movie_id = %s",
            (lobby_id, participant_id, movie_id),
        ):
            connection.execute(
                "INSERT INTO lobby_suggestions "
                "(id, lobby_id, participant_id, movie_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), lobby_id, participant_id, movie_id, int(time.time())),
            )
        connection.execute(
            "UPDATE lobby_members SET is_ready = FALSE WHERE id = %s",
            (member["id"],),
        )

    run_transaction(database, operation, retries)


def launch_lobby(
    database, room_id: str, lobby_id: str, participant_id: str, retries: int = 3
) -> str:
    lobby = database.fetch_one(
        "SELECT rl.filters_json, r.owner_participant_id FROM room_lobbies rl "
        "INNER JOIN rooms r ON rl.room_id = r.id "
        "WHERE rl.id = %s AND rl.room_id = %s",
        (lobby_id, room_id),
    )
    if not lobby or lobby["owner_participant_id"] != participant_id:
        raise AuthorizationError("Only the room owner can start this lobby.")
    members = database.fetch_all(
        "SELECT is_ready FROM lobby_members WHERE lobby_id = %s", (lobby_id,)
    )
    if not members or not all(bool(member["is_ready"]) for member in members):
        raise ConflictError("Everyone must be ready before the session starts.")
    suggestions = database.fetch_all(
        "SELECT movie_id FROM lobby_suggestions WHERE lobby_id = %s", (lobby_id,)
    )
    session_id = start_session(
        database,
        room_id,
        participant_id,
        json.loads(lobby["filters_json"]),
        retries,
        [row["movie_id"] for row in suggestions],
    )

    def remove_lobby(connection):
        connection.execute("DELETE FROM lobby_suggestions WHERE lobby_id = %s", (lobby_id,))
        connection.execute("DELETE FROM lobby_members WHERE lobby_id = %s", (lobby_id,))
        connection.execute("DELETE FROM room_lobbies WHERE id = %s", (lobby_id,))

    run_transaction(database, remove_lobby, retries)
    return session_id


def _delete_session_rows(connection, session_id: str) -> None:
    matches = connection.fetch_all(
        "SELECT id FROM matches WHERE selection_session_id = %s", (session_id,)
    )
    connection.execute(
        "DELETE FROM session_final_votes WHERE selection_session_id = %s", (session_id,)
    )
    connection.execute(
        "DELETE FROM session_finish_votes WHERE selection_session_id = %s", (session_id,)
    )
    connection.execute(
        "DELETE FROM session_member_state WHERE selection_session_id = %s", (session_id,)
    )
    for match in matches:
        connection.execute("DELETE FROM match_members WHERE match_id = %s", (match["id"],))
    connection.execute("DELETE FROM matches WHERE selection_session_id = %s", (session_id,))
    connection.execute("DELETE FROM swipes WHERE selection_session_id = %s", (session_id,))
    connection.execute(
        "DELETE FROM session_movies WHERE selection_session_id = %s", (session_id,)
    )
    connection.execute(
        "DELETE FROM session_members WHERE selection_session_id = %s", (session_id,)
    )
    connection.execute("DELETE FROM selection_sessions WHERE id = %s", (session_id,))


def delete_session(
    database, room_id: str, session_id: str, participant_id: str, retries: int = 3
) -> None:
    def operation(connection):
        room = connection.fetch_one(
            "SELECT owner_participant_id FROM rooms WHERE id = %s", (room_id,)
        )
        if not room or room["owner_participant_id"] != participant_id:
            raise AuthorizationError("Only the room owner can delete sessions.")
        session_row = connection.fetch_one(
            "SELECT id FROM selection_sessions WHERE id = %s AND room_id = %s",
            (session_id, room_id),
        )
        if not session_row:
            raise NotFoundError("Session not found.")
        _delete_session_rows(connection, session_id)

    run_transaction(database, operation, retries)


def cleanup_expired_sessions(database, max_age_seconds: int = 86400) -> int:
    cutoff = int(time.time()) - max_age_seconds
    expired = database.fetch_all(
        "SELECT id FROM selection_sessions WHERE started_at < %s", (cutoff,)
    )
    for row in expired:
        run_transaction(
            database, lambda connection, sid=row["id"]: _delete_session_rows(connection, sid)
        )
    return len(expired)
