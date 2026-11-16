from __future__ import annotations

from flask import Blueprint, Response, abort, current_app, flash, redirect, request, session, stream_with_context, url_for

from app.jellyfin.client import JellyfinUnavailable

from .client import PlexUnavailable
from .coordinator import SyncAlreadyRunning, build_client

bp = Blueprint("plex", __name__)


def _db():
    return current_app.extensions["matcharr_db"]


def _client():
    return build_client(current_app.config)


def _sync():
    return current_app.extensions["media_sync_coordinator"].run_now()


def _provider_name():
    return "Jellyfin" if current_app.config["MEDIA_SERVER_TYPE"] == "jellyfin" else "Plex"


@bp.post("/media/sync")
def sync_action():
    if not session.get("participant_id"):
        abort(401)
    try:
        result = _sync()
    except SyncAlreadyRunning:
        flash(
            f"{_provider_name()} sync is already running.",
            "success",
        )
    except (PlexUnavailable, JellyfinUnavailable, ValueError):
        flash(
            f"{_provider_name()} is unavailable. Existing cached movies remain available.",
            "error",
        )
    else:
        flash(
            f"{_provider_name()} sync complete: {result['movies_received']} movies received.",
            "success",
        )
    return redirect(request.referrer or url_for("rooms.dashboard"))


@bp.post("/plex/sync")
def legacy_sync_action():
    return sync_action()


def _sync_and_print():
    result = _sync()
    print(
        f"Received {result['movies_received']}; created {result['movies_created']}; "
        f"updated {result['movies_updated']}."
    )


@bp.cli.command("media-sync")
def media_sync_command():
    _sync_and_print()


@bp.cli.command("plex-sync")
def sync_command():
    _sync_and_print()


def _image(movie_id, column):
    if not session.get("participant_id"):
        abort(401)
    if column not in {"poster_path", "background_path"}:
        abort(404)
    movie = _db().fetch_one(
        f"SELECT {column} AS image_path FROM movies WHERE id = %s", (movie_id,)
    )
    if not movie or not movie["image_path"]:
        abort(404)
    try:
        upstream = _client().image(movie["image_path"])
    except (PlexUnavailable, JellyfinUnavailable, ValueError):
        abort(502)
    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    if not content_type.startswith("image/"):
        abort(502)
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
        content_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@bp.get("/media/poster/<movie_id>")
def poster(movie_id):
    return _image(movie_id, "poster_path")


@bp.get("/media/background/<movie_id>")
def background(movie_id):
    return _image(movie_id, "background_path")
