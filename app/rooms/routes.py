from __future__ import annotations

import json
import time
from io import BytesIO

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.identity.services import save_identity
from app.matching.services import match_details, session_matches, session_results

from .services import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    archive_room,
    create_room,
    create_lobby,
    delete_session,
    finish_session,
    get_membership,
    join_room,
    launch_lobby,
    leave_room,
    list_rooms,
    remove_member,
    room_details,
    update_room_settings,
)

bp = Blueprint("rooms", __name__)


def _db():
    return current_app.extensions["matcharr_db"]


def _participant() -> tuple[str, str] | None:
    participant_id = session.get("participant_id")
    display_name = session.get("display_name")
    return (participant_id, display_name) if participant_id and display_name else None


def _require_identity():
    identity = _participant()
    if not identity:
        return None, redirect(url_for("identity.index"))
    return identity, None


def _quick_join_url(invite_code: str) -> str:
    path = url_for("rooms.quick_join", invite_code=invite_code)
    public_url = current_app.config.get("MATCHARR_PUBLIC_URL", "")
    return f"{public_url}{path}" if public_url else url_for(
        "rooms.quick_join", invite_code=invite_code, _external=True
    )


@bp.get("/dashboard")
@bp.get("/rooms")
def dashboard():
    identity, response = _require_identity()
    if response:
        return response
    return render_template(
        "dashboard.html",
        rooms=list_rooms(_db(), identity[0]),
        display_name=identity[1],
    )


@bp.get("/rooms/create")
def create_page():
    _, response = _require_identity()
    return response or render_template("create_room.html")


@bp.post("/rooms/create")
def create_action():
    identity, response = _require_identity()
    if response:
        return response
    try:
        room = create_room(
            _db(),
            identity[0],
            identity[1],
            request.form.get("name"),
            request.form.get("include_watched") == "on",
            current_app.config["MATCHARR_INVITE_CODE_LENGTH"],
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except ValueError as error:
        flash(str(error), "error")
        return render_template("create_room.html"), 400
    return redirect(url_for("rooms.room_page", room_id=room["id"]))


@bp.get("/rooms/join")
def join_page():
    _, response = _require_identity()
    return response or render_template("join_room.html")


def _invite_attempt_allowed() -> bool:
    now = int(time.time())
    window = current_app.config["MATCHARR_INVITE_WINDOW_SECONDS"]
    attempts = [
        timestamp
        for timestamp in session.get("invite_attempts", [])
        if now - timestamp < window
    ]
    session["invite_attempts"] = attempts
    return len(attempts) < current_app.config["MATCHARR_INVITE_ATTEMPTS"]


@bp.post("/rooms/join")
def join_action():
    identity, response = _require_identity()
    if response:
        return response
    if not _invite_attempt_allowed():
        flash("Too many invalid invite attempts. Please wait a few minutes.", "error")
        return render_template("join_room.html"), 429
    try:
        room = join_room(
            _db(),
            identity[0],
            identity[1],
            request.form.get("invite_code"),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except (ValueError, ConflictError, NotFoundError) as error:
        attempts = session.get("invite_attempts", [])
        attempts.append(int(time.time()))
        session["invite_attempts"] = attempts
        flash(str(error), "error")
        return render_template("join_room.html"), 400
    session.pop("invite_attempts", None)
    return redirect(url_for("rooms.room_page", room_id=room["id"]))


@bp.get("/rooms/<room_id>")
def room_page(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        details = room_details(_db(), room_id, identity[0])
    except AuthorizationError:
        abort(403)
    return render_template(
        "room.html",
        room_id=room_id,
        quick_join_url=_quick_join_url(details["membership"]["invite_code"]),
        **details,
    )


@bp.route("/join/<invite_code>", methods=["GET", "POST"])
def quick_join(invite_code):
    code = (invite_code or "").strip().upper()
    room = _db().fetch_one(
        "SELECT id, name, is_archived FROM rooms WHERE invite_code = %s", (code,)
    )
    if not room or room["is_archived"]:
        return render_template(
            "quick_join.html", room=None, invite_code=code, error="This invite is invalid or expired."
        ), 404

    participant_id = session.get("participant_id")
    display_name = session.get("display_name")
    if request.method == "POST":
        display_name = request.form.get("display_name", "")
        try:
            participant_id = save_identity(
                _db(),
                participant_id,
                display_name,
                current_app.config["MATCHARR_TRANSACTION_RETRIES"],
            )
        except ValueError as error:
            return render_template(
                "quick_join.html",
                room=room,
                invite_code=code,
                display_name=display_name,
                error=str(error),
            ), 400
        session["participant_id"] = participant_id
        session["display_name"] = display_name.strip()
        session.permanent = True

    if participant_id and display_name:
        try:
            joined = join_room(
                _db(),
                participant_id,
                display_name,
                code,
                current_app.config["MATCHARR_TRANSACTION_RETRIES"],
            )
        except (ConflictError, NotFoundError, ValueError) as error:
            return render_template(
                "quick_join.html",
                room=room,
                invite_code=code,
                display_name=display_name,
                error=str(error),
            ), 400
        return redirect(url_for("rooms.room_page", room_id=joined["id"]))

    return render_template(
        "quick_join.html",
        room=room,
        invite_code=code,
        display_name="",
        error=None,
    )


@bp.get("/rooms/<room_id>/invite-qr.svg")
def invite_qr(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        membership = get_membership(_db(), room_id, identity[0])
    except AuthorizationError:
        abort(403)
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(_quick_join_url(membership["invite_code"]))
    qr.make(fit=True)
    output = BytesIO()
    qr.make_image(image_factory=SvgPathImage).save(output)
    return Response(
        output.getvalue(),
        content_type="image/svg+xml",
        headers={"Cache-Control": "private, no-store"},
    )


@bp.post("/rooms/<room_id>/leave")
def leave_action(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        leave_room(
            _db(), room_id, identity[0], current_app.config["MATCHARR_TRANSACTION_RETRIES"]
        )
    except (AuthorizationError, ConflictError) as error:
        flash(str(error), "error")
        return redirect(url_for("rooms.room_page", room_id=room_id))
    return redirect(url_for("rooms.dashboard"))


@bp.post("/rooms/<room_id>/archive")
def archive_action(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        archive_room(
            _db(), room_id, identity[0], current_app.config["MATCHARR_TRANSACTION_RETRIES"]
        )
    except AuthorizationError:
        abort(403)
    return redirect(url_for("rooms.dashboard"))


@bp.post("/rooms/<room_id>/settings")
def settings_action(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        update_room_settings(
            _db(),
            room_id,
            identity[0],
            request.form.get("name", ""),
            request.form.get("include_watched") == "on",
            _filters_from_form(),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except ValueError as error:
        flash(str(error), "error")
    else:
        flash("Room defaults updated. Active sessions keep their frozen pool.", "success")
    return redirect(url_for("rooms.room_page", room_id=room_id))


@bp.post("/rooms/<room_id>/members/<member_id>/remove")
def remove_member_action(room_id, member_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        remove_member(
            _db(),
            room_id,
            identity[0],
            member_id,
            request.form.get("remove_from_session") == "on",
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except ConflictError as error:
        flash(str(error), "error")
    return redirect(url_for("rooms.room_page", room_id=room_id))


def _filters_from_form() -> dict:
    def integer(name, default=None):
        raw = request.form.get(name, "").strip()
        return int(raw) if raw else default

    def number(name, default=None):
        raw = request.form.get(name, "").strip()
        return float(raw) if raw else default

    feed = request.form.get("feed", "mixed")
    mix_recently_added = integer("mix_recently_added", 25)
    mix_recently_released = integer("mix_recently_released", 25)
    mix_random = integer("mix_random", 50)
    mix_values = (mix_recently_added, mix_recently_released, mix_random)
    if any(value < 0 or value > 100 for value in mix_values):
        raise ValueError("Movie mix sliders must be between 0 and 100.")
    if feed == "mixed" and sum(mix_values) <= 0:
        raise ValueError("At least one movie mix slider must be above zero.")

    return {
        "genres": [value.strip() for value in request.form.get("genres", "").split(",") if value.strip()],
        "excluded_genres": [
            value.strip()
            for value in request.form.get("excluded_genres", "").split(",")
            if value.strip()
        ],
        "minimum_audience_rating": number("minimum_audience_rating"),
        "maximum_runtime": integer("maximum_runtime"),
        "earliest_release_year": integer("earliest_release_year"),
        "latest_release_year": integer("latest_release_year"),
        "recently_added_only": request.form.get("recently_added_only") == "on",
        "popular_only": request.form.get("popular_only") == "on",
        "feed": feed,
        "candidate_pool_size": integer("candidate_pool_size", 50),
        "mix_recently_added": mix_recently_added,
        "mix_recently_released": mix_recently_released,
        "mix_random": mix_random,
        "media_source": current_app.config["MEDIA_SERVER_TYPE"],
    }


@bp.post("/rooms/<room_id>/sessions")
def start_session_action(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        create_lobby(
            _db(),
            room_id,
            identity[0],
            _filters_from_form(),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except (ConflictError, ValueError, json.JSONDecodeError) as error:
        flash(str(error), "error")
        return redirect(url_for("rooms.room_page", room_id=room_id))
    return redirect(url_for("rooms.room_page", room_id=room_id))


@bp.post("/rooms/<room_id>/lobbies/<lobby_id>/start")
def launch_lobby_action(room_id, lobby_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        session_id = launch_lobby(
            _db(),
            room_id,
            lobby_id,
            identity[0],
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except (ConflictError, NotFoundError, ValueError) as error:
        flash(str(error), "error")
        return redirect(url_for("rooms.room_page", room_id=room_id))
    return redirect(url_for("rooms.swipe_page", room_id=room_id, session_id=session_id))


@bp.post("/rooms/<room_id>/sessions/<session_id>/delete")
def delete_session_action(room_id, session_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        delete_session(
            _db(),
            room_id,
            session_id,
            identity[0],
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except NotFoundError as error:
        flash(str(error), "error")
    else:
        flash("Session permanently deleted.", "success")
    return redirect(url_for("rooms.room_page", room_id=room_id))


@bp.post("/rooms/<room_id>/sessions/<session_id>/finish")
def finish_session_action(room_id, session_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        finish_session(
            _db(),
            room_id,
            session_id,
            identity[0],
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except AuthorizationError:
        abort(403)
    except NotFoundError as error:
        flash(str(error), "error")
    return redirect(url_for("rooms.room_page", room_id=room_id))


@bp.get("/rooms/<room_id>/swipe")
def swipe_page(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        details = room_details(_db(), room_id, identity[0])
    except AuthorizationError:
        abort(403)
    session_id = request.args.get("session_id") or (
        details["active_session"]["id"] if details["active_session"] else None
    )
    if not session_id:
        return redirect(url_for("rooms.room_page", room_id=room_id))
    initial_matches = session_matches(_db(), room_id, session_id, identity[0])
    return render_template(
        "swipe.html",
        room_id=room_id,
        session_id=session_id,
        membership=details["membership"],
        poll_interval=current_app.config["MATCHARR_POLL_INTERVAL_MS"],
        swipe_distance=current_app.config["MATCHARR_SWIPE_DISTANCE_THRESHOLD"],
        swipe_velocity=current_app.config["MATCHARR_SWIPE_VELOCITY_THRESHOLD"],
        initial_match_ids=[match["id"] for match in initial_matches],
    )


@bp.get("/rooms/<room_id>/matches")
def matches_page(room_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        details = room_details(_db(), room_id, identity[0])
    except AuthorizationError:
        abort(403)
    session_id = request.args.get("session_id")
    if session_id:
        result = session_results(_db(), room_id, session_id, identity[0])
        matches = result["matches"]
    else:
        matches = _db().fetch_all(
            "SELECT ma.id, ma.movie_id, ma.matched_at, m.title, m.release_year "
            "FROM matches ma INNER JOIN movies m ON ma.movie_id = m.id "
            "WHERE ma.room_id = %s ORDER BY ma.matched_at DESC",
            (room_id,),
        )
        result = {
            "votes_cast": 0,
            "eligible_members": 0,
            "current_vote": None,
            "winner": None,
        }
    return render_template(
        "matches.html",
        room_id=room_id,
        matches=matches,
        session_results=bool(session_id),
        session_id=session_id,
        result=result,
        **details,
    )


@bp.get("/rooms/<room_id>/matches/<match_id>")
def match_page(room_id, match_id):
    identity, response = _require_identity()
    if response:
        return response
    try:
        details = room_details(_db(), room_id, identity[0])
    except AuthorizationError:
        abort(403)
    match = match_details(_db(), match_id, room_id)
    if not match:
        abort(404)
    return render_template(
        "match.html",
        room_id=room_id,
        match=match,
        membership=details["membership"],
        active_session=details["active_session"],
    )
