from __future__ import annotations

import json
import time
import uuid

from app.database.transaction import run_transaction


def sync_movies(database, client, retries: int = 3) -> dict:
    provider_name = getattr(client, "provider_name", "Plex")
    run_id = str(uuid.uuid4())
    started = int(time.time())
    database.execute(
        "INSERT INTO plex_sync_runs "
        "(id, status, movies_received, movies_created, movies_updated, error_message, "
        "started_at, completed_at) VALUES (%s, 'running', 0, 0, 0, NULL, %s, NULL)",
        (run_id, started),
    )
    try:
        movies = client.movies()

        def operation(connection):
            created = 0
            updated = 0
            now = int(time.time())
            for movie in movies:
                watched_literal = "TRUE" if movie["watched"] else "FALSE"
                existing = connection.fetch_one(
                    "SELECT id FROM movies WHERE plex_rating_key = %s",
                    (movie["plex_rating_key"],),
                )
                values = (
                    movie["title"],
                    movie["release_year"],
                    movie["summary"],
                    movie["poster_path"],
                    movie["background_path"],
                    movie["duration_minutes"],
                    movie["content_rating"],
                    movie["audience_rating"],
                    movie["critic_rating"],
                    json.dumps(movie["genres"], separators=(",", ":")),
                    movie["date_added"],
                    movie["view_count"],
                    movie["watched"],
                    movie["plex_watch_url"],
                    json.dumps(movie["metadata"], separators=(",", ":"), sort_keys=True),
                    now,
                )
                if existing:
                    connection.execute(
                        "UPDATE movies SET title = %s, release_year = %s, summary = %s, "
                        "poster_path = %s, background_path = %s, duration_minutes = %s, "
                        "content_rating = %s, audience_rating = %s, critic_rating = %s, "
                        f"genres_json = %s, date_added = %s, view_count = %s, watched = {watched_literal}, "
                        "plex_watch_url = %s, metadata_json = %s, updated_at = %s WHERE id = %s",
                        values[:12] + values[13:] + (existing["id"],),
                    )
                    updated += 1
                else:
                    connection.execute(
                        "INSERT INTO movies "
                        "(id, plex_rating_key, title, release_year, summary, poster_path, "
                        "background_path, duration_minutes, content_rating, audience_rating, "
                        "critic_rating, genres_json, date_added, view_count, watched, "
                        "plex_watch_url, metadata_json, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        f"%s, {watched_literal}, %s, %s, %s, %s)",
                        (
                            str(uuid.uuid4()),
                            movie["plex_rating_key"],
                        )
                        + values[:12]
                        + values[13:-1]
                        + (now, now),
                    )
                    created += 1
            connection.execute(
                "UPDATE plex_sync_runs SET status = 'success', movies_received = %s, "
                "movies_created = %s, movies_updated = %s, completed_at = %s WHERE id = %s",
                (len(movies), created, updated, now, run_id),
            )
            return {
                "id": run_id,
                "status": "success",
                "movies_received": len(movies),
                "movies_created": created,
                "movies_updated": updated,
            }

        return run_transaction(database, operation, retries)
    except Exception as error:
        safe_error = f"{provider_name} synchronization failed."
        database.execute(
            "UPDATE plex_sync_runs SET status = 'failed', error_message = %s, "
            "completed_at = %s WHERE id = %s",
            (safe_error, int(time.time()), run_id),
        )
        raise
