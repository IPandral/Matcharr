from __future__ import annotations

import time
import uuid

from app.database.transaction import run_transaction
from app.rooms.services import AuthorizationError, ConflictError, NotFoundError


def next_movie(database, room_id: str, session_id: str, participant_id: str) -> dict | None:
    def operation(connection):
        eligible = connection.fetch_one(
            "SELECT sm.id FROM session_members sm "
            "INNER JOIN selection_sessions ss ON sm.selection_session_id = ss.id "
            "WHERE sm.selection_session_id = %s AND sm.participant_id = %s "
            "AND sm.is_eligible = TRUE AND ss.room_id = %s AND ss.status = 'active'",
            (session_id, participant_id, room_id),
        )
        if not eligible:
            raise AuthorizationError("You are not eligible for this session.")
        state = connection.fetch_one(
            "SELECT id, current_movie_id FROM session_member_state "
            "WHERE selection_session_id = %s AND participant_id = %s",
            (session_id, participant_id),
        )
        if state and state.get("current_movie_id"):
            current_movie_id = state["current_movie_id"]
            in_pool = connection.fetch_one(
                "SELECT id FROM session_movies WHERE selection_session_id = %s "
                "AND movie_id = %s",
                (session_id, current_movie_id),
            )
            already_swiped = connection.fetch_one(
                "SELECT id FROM swipes WHERE selection_session_id = %s "
                "AND participant_id = %s AND movie_id = %s",
                (session_id, participant_id, current_movie_id),
            )
            if in_pool and not already_swiped:
                return current_movie_id
        candidate = connection.fetch_one(
            "SELECT movie_id FROM session_movies WHERE selection_session_id = %s "
            "AND movie_id NOT IN ("
            "SELECT movie_id FROM swipes WHERE selection_session_id = %s "
            "AND participant_id = %s"
            ") ORDER BY position ASC LIMIT 1",
            (session_id, session_id, participant_id),
        )
        movie_id = candidate["movie_id"] if candidate else None
        now = int(time.time())
        if state:
            connection.execute(
                "UPDATE session_member_state SET current_movie_id = %s, updated_at = %s "
                "WHERE id = %s",
                (movie_id, now, state["id"]),
            )
        else:
            connection.execute(
                "INSERT INTO session_member_state "
                "(id, selection_session_id, participant_id, current_movie_id, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), session_id, participant_id, movie_id, now),
            )
        return movie_id

    movie_id = run_transaction(database, operation)
    if not movie_id:
        return None
    # RustyDB 0.3.0-beta reports joined wildcard columns as VAR_STRING in
    # prepared metadata. Fetching the typed movie row from its base table keeps
    # Connector/Python's binary decoding correct.
    return database.fetch_one("SELECT * FROM movies WHERE id = %s", (movie_id,))


def record_swipe(database, room_id: str, session_id: str, participant_id: str,
                 movie_id: str, decision: str, idempotency_key: str, retries: int = 3) -> dict:
    if decision not in {"like", "skip"}:
        raise ValueError("Decision must be like or skip.")
    try:
        uuid.UUID(idempotency_key)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A valid idempotency key is required.") from error

    def operation(connection):
        session_row = connection.fetch_one(
            "SELECT id, status FROM selection_sessions WHERE id = %s AND room_id = %s",
            (session_id, room_id),
        )
        if not session_row:
            raise NotFoundError("Session not found.")
        eligible = connection.fetch_one(
            "SELECT id FROM session_members WHERE selection_session_id = %s "
            "AND participant_id = %s AND is_eligible = TRUE",
            (session_id, participant_id),
        )
        if not eligible:
            raise AuthorizationError("You are not eligible for this session.")
        if not connection.fetch_one(
            "SELECT id FROM session_movies WHERE selection_session_id = %s AND movie_id = %s",
            (session_id, movie_id),
        ):
            raise AuthorizationError("That movie is not in this session.")
        prior_request = connection.fetch_one(
            "SELECT movie_id, decision FROM swipes WHERE idempotency_key = %s "
            "AND selection_session_id = %s AND participant_id = %s",
            (idempotency_key, session_id, participant_id),
        )
        if prior_request:
            if prior_request["movie_id"] != movie_id or prior_request["decision"] != decision:
                raise ConflictError("Idempotency key was already used for another decision.")
            match = connection.fetch_one(
                "SELECT id FROM matches WHERE selection_session_id = %s AND movie_id = %s",
                (session_id, movie_id),
            )
            connection.execute(
                "UPDATE session_member_state SET current_movie_id = NULL, updated_at = %s "
                "WHERE selection_session_id = %s AND participant_id = %s",
                (int(time.time()), session_id, participant_id),
            )
            return {
                "matched": bool(match),
                "match_id": match["id"] if match else None,
                "replayed": True,
                "session_completed": session_row["status"] == "completed",
            }
        if session_row["status"] != "active":
            raise NotFoundError("Active session not found.")
        prior = connection.fetch_one(
            "SELECT decision FROM swipes WHERE selection_session_id = %s "
            "AND participant_id = %s AND movie_id = %s",
            (session_id, participant_id, movie_id),
        )
        if prior:
            raise ConflictError("You have already swiped on this movie.")
        now = int(time.time())
        connection.execute(
            "INSERT INTO swipes "
            "(id, selection_session_id, participant_id, movie_id, decision, "
            "idempotency_key, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                session_id,
                participant_id,
                movie_id,
                decision,
                idempotency_key,
                now,
            ),
        )
        connection.execute(
            "UPDATE session_member_state SET current_movie_id = NULL, updated_at = %s "
            "WHERE selection_session_id = %s AND participant_id = %s",
            (now, session_id, participant_id),
        )
        match_id = None
        if decision == "like":
            eligible_count = connection.fetch_one(
                "SELECT COUNT(*) AS total FROM session_members "
                "WHERE selection_session_id = %s AND is_eligible = TRUE",
                (session_id,),
            )["total"]
            likes = connection.fetch_one(
                "SELECT COUNT(*) AS total FROM swipes s "
                "INNER JOIN session_members sm "
                "ON sm.selection_session_id = s.selection_session_id "
                "AND sm.participant_id = s.participant_id "
                "WHERE s.selection_session_id = %s AND s.movie_id = %s "
                "AND s.decision = 'like' AND sm.is_eligible = TRUE",
                (session_id, movie_id),
            )["total"]
            skips = connection.fetch_one(
                "SELECT COUNT(*) AS total FROM swipes s "
                "INNER JOIN session_members sm "
                "ON sm.selection_session_id = s.selection_session_id "
                "AND sm.participant_id = s.participant_id "
                "WHERE s.selection_session_id = %s AND s.movie_id = %s "
                "AND s.decision = 'skip' AND sm.is_eligible = TRUE",
                (session_id, movie_id),
            )["total"]
            if int(likes) == int(eligible_count) and int(skips) == 0:
                existing_match = connection.fetch_one(
                    "SELECT id FROM matches WHERE selection_session_id = %s AND movie_id = %s",
                    (session_id, movie_id),
                )
                if existing_match:
                    match_id = existing_match["id"]
                else:
                    match_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO matches "
                        "(id, selection_session_id, room_id, movie_id, status, matched_at) "
                        "VALUES (%s, %s, %s, %s, 'active', %s)",
                        (match_id, session_id, room_id, movie_id, now),
                    )
                    liked_members = connection.fetch_all(
                        "SELECT sm.participant_id, sm.display_name "
                        "FROM session_members sm INNER JOIN swipes s "
                        "ON s.selection_session_id = sm.selection_session_id "
                        "AND s.participant_id = sm.participant_id "
                        "WHERE sm.selection_session_id = %s AND sm.is_eligible = TRUE "
                        "AND s.movie_id = %s AND s.decision = 'like'",
                        (session_id, movie_id),
                    )
                    for member in liked_members:
                        connection.execute(
                            "INSERT INTO match_members "
                            "(id, match_id, participant_id, display_name, liked_at) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (
                                str(uuid.uuid4()),
                                match_id,
                                member["participant_id"],
                                member["display_name"],
                                now,
                            ),
                        )
        pool_count = int(
            connection.fetch_one(
                "SELECT COUNT(*) AS total FROM session_movies "
                "WHERE selection_session_id = %s",
                (session_id,),
            )["total"]
        )
        eligible_total = int(
            connection.fetch_one(
                "SELECT COUNT(*) AS total FROM session_members "
                "WHERE selection_session_id = %s AND is_eligible = TRUE",
                (session_id,),
            )["total"]
        )
        decision_count = int(
            connection.fetch_one(
                "SELECT COUNT(*) AS total FROM swipes s "
                "INNER JOIN session_members sm "
                "ON sm.selection_session_id = s.selection_session_id "
                "AND sm.participant_id = s.participant_id "
                "WHERE s.selection_session_id = %s AND sm.is_eligible = TRUE",
                (session_id,),
            )["total"]
        )
        session_completed = (
            pool_count > 0
            and eligible_total > 0
            and decision_count >= pool_count * eligible_total
        )
        if session_completed:
            connection.execute(
                "UPDATE selection_sessions SET status = 'completed', completed_at = %s "
                "WHERE id = %s AND status = 'active'",
                (now, session_id),
            )
        return {
            "matched": bool(match_id),
            "match_id": match_id,
            "replayed": False,
            "session_completed": session_completed,
        }

    return run_transaction(database, operation, retries)


def session_progress(
    database, room_id: str, session_id: str, participant_id: str
) -> dict:
    eligible = database.fetch_one(
        "SELECT sm.id, ss.status FROM session_members sm "
        "INNER JOIN selection_sessions ss ON sm.selection_session_id = ss.id "
        "WHERE sm.selection_session_id = %s AND sm.participant_id = %s "
        "AND sm.is_eligible = TRUE AND ss.room_id = %s",
        (session_id, participant_id, room_id),
    )
    if not eligible:
        raise AuthorizationError("You are not eligible for this session.")
    total = int(
        database.fetch_one(
            "SELECT COUNT(*) AS total FROM session_movies WHERE selection_session_id = %s",
            (session_id,),
        )["total"]
    )
    members = database.fetch_all(
        "SELECT participant_id, display_name FROM session_members "
        "WHERE selection_session_id = %s AND is_eligible = TRUE ORDER BY added_at ASC",
        (session_id,),
    )
    finish_votes = database.fetch_all(
        "SELECT participant_id FROM session_finish_votes WHERE selection_session_id = %s",
        (session_id,),
    )
    eligible_ids = {row["participant_id"] for row in members}
    valid_votes = {row["participant_id"] for row in finish_votes} & eligible_ids
    member_progress = []
    decided = 0
    for member in members:
        member_decided = int(
            database.fetch_one(
                "SELECT COUNT(*) AS total FROM swipes WHERE selection_session_id = %s "
                "AND participant_id = %s",
                (session_id, member["participant_id"]),
            )["total"]
        )
        if member["participant_id"] == participant_id:
            decided = member_decided
        member_progress.append(
            {
                "participant_id": member["participant_id"],
                "display_name": member["display_name"],
                "decided": min(member_decided, total),
                "total": total,
            }
        )
    return {
        "total": total,
        "decided": min(decided, total),
        "remaining": max(total - decided, 0),
        "finish_votes": len(valid_votes),
        "eligible_members": len(members),
        "current_participant_voted": participant_id in valid_votes,
        "session_status": eligible["status"],
        "members": member_progress,
    }


def vote_to_finish(
    database,
    room_id: str,
    session_id: str,
    participant_id: str,
    retries: int = 3,
) -> dict:
    def operation(connection):
        session_row = connection.fetch_one(
            "SELECT id FROM selection_sessions WHERE id = %s AND room_id = %s "
            "AND status = 'active'",
            (session_id, room_id),
        )
        if not session_row:
            raise NotFoundError("Active session not found.")
        eligible_rows = connection.fetch_all(
            "SELECT participant_id FROM session_members WHERE selection_session_id = %s "
            "AND is_eligible = TRUE",
            (session_id,),
        )
        eligible_ids = {row["participant_id"] for row in eligible_rows}
        if participant_id not in eligible_ids:
            raise AuthorizationError("You are not eligible for this session.")
        existing = connection.fetch_one(
            "SELECT id FROM session_finish_votes WHERE selection_session_id = %s "
            "AND participant_id = %s",
            (session_id, participant_id),
        )
        now = int(time.time())
        if not existing:
            connection.execute(
                "INSERT INTO session_finish_votes "
                "(id, selection_session_id, participant_id, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), session_id, participant_id, now),
            )
        vote_rows = connection.fetch_all(
            "SELECT participant_id FROM session_finish_votes WHERE selection_session_id = %s",
            (session_id,),
        )
        valid_votes = {row["participant_id"] for row in vote_rows} & eligible_ids
        completed = bool(eligible_ids) and valid_votes == eligible_ids
        if completed:
            connection.execute(
                "UPDATE selection_sessions SET status = 'completed', completed_at = %s "
                "WHERE id = %s",
                (now, session_id),
            )
        return {
            "completed": completed,
            "finish_votes": len(valid_votes),
            "eligible_members": len(eligible_ids),
        }

    return run_transaction(database, operation, retries)


def match_details(database, match_id: str, room_id: str) -> dict | None:
    match = database.fetch_one(
        "SELECT ma.id, ma.movie_id, ma.matched_at, m.title, m.release_year, "
        "m.plex_watch_url FROM matches ma INNER JOIN movies m ON ma.movie_id = m.id "
        "WHERE ma.id = %s AND ma.room_id = %s",
        (match_id, room_id),
    )
    if not match:
        return None
    match["members"] = database.fetch_all(
        "SELECT display_name FROM match_members WHERE match_id = %s ORDER BY liked_at ASC",
        (match_id,),
    )
    return match


def session_matches(database, room_id: str, session_id: str, participant_id: str) -> list[dict]:
    if not database.fetch_one(
        "SELECT id FROM room_members WHERE room_id = %s AND participant_id = %s "
        "AND is_active = TRUE",
        (room_id, participant_id),
    ):
        raise AuthorizationError("You are not an active room member.")
    return database.fetch_all(
        "SELECT ma.id, ma.movie_id, ma.matched_at, m.title, m.release_year, "
        "m.audience_rating, m.critic_rating "
        "FROM matches ma INNER JOIN movies m ON ma.movie_id = m.id "
        "WHERE ma.room_id = %s AND ma.selection_session_id = %s "
        "ORDER BY m.audience_rating DESC, m.critic_rating DESC, ma.matched_at ASC",
        (room_id, session_id),
    )


def undo_last_swipe(
    database,
    room_id: str,
    session_id: str,
    participant_id: str,
    retries: int = 3,
) -> dict:
    def operation(connection):
        eligible = connection.fetch_one(
            "SELECT sm.id FROM session_members sm INNER JOIN selection_sessions ss "
            "ON sm.selection_session_id = ss.id WHERE sm.selection_session_id = %s "
            "AND sm.participant_id = %s AND sm.is_eligible = TRUE "
            "AND ss.room_id = %s AND ss.status = 'active'",
            (session_id, participant_id, room_id),
        )
        if not eligible:
            raise AuthorizationError("You are not eligible for this active session.")
        swipe = connection.fetch_one(
            "SELECT id, movie_id, decision FROM swipes WHERE selection_session_id = %s "
            "AND participant_id = %s ORDER BY created_at DESC LIMIT 1",
            (session_id, participant_id),
        )
        if not swipe:
            raise NotFoundError("There is no swipe to undo.")
        if connection.fetch_one(
            "SELECT id FROM matches WHERE selection_session_id = %s AND movie_id = %s",
            (session_id, swipe["movie_id"]),
        ):
            raise ConflictError("A confirmed group match cannot be undone.")
        connection.execute("DELETE FROM swipes WHERE id = %s", (swipe["id"],))
        connection.execute(
            "UPDATE session_member_state SET current_movie_id = %s, updated_at = %s "
            "WHERE selection_session_id = %s AND participant_id = %s",
            (swipe["movie_id"], int(time.time()), session_id, participant_id),
        )
        return {"movie_id": swipe["movie_id"], "decision": swipe["decision"]}

    return run_transaction(database, operation, retries)


def vote_for_final_match(
    database,
    room_id: str,
    session_id: str,
    participant_id: str,
    match_id: str,
    retries: int = 3,
) -> None:
    def operation(connection):
        eligible = connection.fetch_one(
            "SELECT sm.id FROM session_members sm INNER JOIN selection_sessions ss "
            "ON sm.selection_session_id = ss.id WHERE sm.selection_session_id = %s "
            "AND sm.participant_id = %s AND sm.is_eligible = TRUE "
            "AND ss.room_id = %s AND ss.status = 'completed'",
            (session_id, participant_id, room_id),
        )
        if not eligible:
            raise AuthorizationError("Final voting is available to completed-session members.")
        if not connection.fetch_one(
            "SELECT id FROM matches WHERE id = %s AND selection_session_id = %s "
            "AND room_id = %s",
            (match_id, session_id, room_id),
        ):
            raise NotFoundError("That match is not part of this session.")
        existing = connection.fetch_one(
            "SELECT id FROM session_final_votes WHERE selection_session_id = %s "
            "AND participant_id = %s",
            (session_id, participant_id),
        )
        if existing:
            connection.execute(
                "UPDATE session_final_votes SET match_id = %s, created_at = %s WHERE id = %s",
                (match_id, int(time.time()), existing["id"]),
            )
        else:
            connection.execute(
                "INSERT INTO session_final_votes "
                "(id, selection_session_id, participant_id, match_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    participant_id,
                    match_id,
                    int(time.time()),
                ),
            )

    run_transaction(database, operation, retries)


def session_results(
    database, room_id: str, session_id: str, participant_id: str
) -> dict:
    matches = session_matches(database, room_id, session_id, participant_id)
    eligible = database.fetch_all(
        "SELECT participant_id FROM session_members WHERE selection_session_id = %s "
        "AND is_eligible = TRUE",
        (session_id,),
    )
    votes = database.fetch_all(
        "SELECT participant_id, match_id FROM session_final_votes "
        "WHERE selection_session_id = %s",
        (session_id,),
    )
    counts = {}
    for vote in votes:
        counts[vote["match_id"]] = counts.get(vote["match_id"], 0) + 1
    current_vote = next(
        (vote["match_id"] for vote in votes if vote["participant_id"] == participant_id),
        None,
    )
    for match in matches:
        match["final_votes"] = counts.get(match["id"], 0)
    matches.sort(
        key=lambda match: (
            -match["final_votes"],
            -(float(match.get("audience_rating") or 0)),
            -(float(match.get("critic_rating") or 0)),
            int(match["matched_at"]),
        )
    )
    winner = None
    if matches and len(votes) >= len(eligible):
        top = matches[0]["final_votes"]
        if top > 0 and sum(match["final_votes"] == top for match in matches) == 1:
            winner = matches[0]
    return {
        "matches": matches,
        "votes_cast": len(votes),
        "eligible_members": len(eligible),
        "current_vote": current_vote,
        "winner": winner,
    }
