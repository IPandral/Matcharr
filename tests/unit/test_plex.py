from __future__ import annotations

import threading

import pytest

from app import create_app
from app.jellyfin.client import JellyfinConfig, JellyfinHTTPClient, JellyfinUnavailable
from app.plex.client import PlexConfig, PlexHTTPClient, PlexUnavailable
from app.plex.routes import _client
from app.plex.coordinator import MediaSyncCoordinator, SyncAlreadyRunning, _FileLock
from app.plex.sync import sync_movies


class Response:
    content = b"""<MediaContainer machineIdentifier="server">
      <Video ratingKey="42" title="Arrival" year="2016" duration="6960000"
        contentRating="PG-13" audienceRating="8.1" rating="7.9"
        addedAt="1700000000" viewCount="0" thumb="/thumb/42" art="/art/42"
        summary="A linguist meets visitors.">
        <Genre tag="Drama"/><Genre tag="Science Fiction"/>
      </Video>
    </MediaContainer>"""

    def raise_for_status(self):
        return None


class HTTP:
    @staticmethod
    def get(*args, **kwargs):
        return Response()


def test_plex_metadata_conversion_and_token_stays_in_header():
    client = PlexHTTPClient(
        PlexConfig("http://plex:32400", "secret-token", "1", "Movies"), HTTP
    )
    movie = client.movies()[0]
    assert movie["plex_rating_key"] == "42"
    assert movie["duration_minutes"] == 116
    assert movie["genres"] == ["Drama", "Science Fiction"]
    assert movie["watched"] is False
    assert "secret-token" not in str(movie)


def test_plex_connection_failure_is_wrapped():
    class BrokenHTTP:
        @staticmethod
        def get(*args, **kwargs):
            import requests
            raise requests.ConnectionError("token=must-not-leak")

    client = PlexHTTPClient(
        PlexConfig("http://plex:32400", "secret-token", "1", "Movies"), BrokenHTTP
    )
    with pytest.raises(PlexUnavailable, match="could not be reached"):
        client.movies()


def test_jellyfin_metadata_conversion_library_lookup_and_token_header():
    calls = []

    class JellyfinResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class JellyfinHTTP:
        @staticmethod
        def get(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/Views"):
                return JellyfinResponse(
                    {"Items": [{"Id": "library-1", "Name": "Movies"}]}
                )
            return JellyfinResponse(
                {
                    "Items": [
                        {
                            "Id": "movie-42",
                            "Name": "Arrival",
                            "ProductionYear": 2016,
                            "Overview": "Visitors arrive.",
                            "RunTimeTicks": 69_600_000_000,
                            "OfficialRating": "PG-13",
                            "CommunityRating": 8.1,
                            "CriticRating": 7.9,
                            "Genres": ["Drama", "Science Fiction"],
                            "DateCreated": "2025-01-02T03:04:05Z",
                            "PremiereDate": "2016-09-01T00:00:00Z",
                            "ImageTags": {"Primary": "tag"},
                            "BackdropImageTags": ["backdrop"],
                            "UserData": {"PlayCount": 2, "Played": True},
                            "ProviderIds": {"Imdb": "tt2543164"},
                        }
                    ]
                }
            )

    client = JellyfinHTTPClient(
        JellyfinConfig(
            "http://jellyfin:8096",
            "secret-api-key",
            "user-1",
            "",
            "Movies",
        ),
        JellyfinHTTP,
    )
    movie = client.movies()[0]

    assert movie["plex_rating_key"] == "jellyfin:movie-42"
    assert movie["duration_minutes"] == 116
    assert movie["watched"] is True
    assert movie["view_count"] == 2
    assert movie["poster_path"] == "/Items/movie-42/Images/Primary"
    assert movie["metadata"]["source"] == "jellyfin"
    assert all(
        kwargs["headers"]["Authorization"] == 'MediaBrowser Token="secret-api-key"'
        for _, kwargs in calls
    )
    assert "secret-api-key" not in str(movie)
    assert "secret-api-key" not in str([url for url, _ in calls])


def test_jellyfin_requires_url_key_and_user():
    client = JellyfinHTTPClient(JellyfinConfig("", "", "", "", "Movies"))
    with pytest.raises(JellyfinUnavailable, match="must be configured"):
        client.movies()


def test_media_server_type_is_validated(scripted_db):
    with pytest.raises(ValueError, match="plex.*jellyfin"):
        create_app({"MEDIA_SERVER_TYPE": "emby"}, database=scripted_db)


def test_media_client_factory_selects_jellyfin(scripted_db):
    app = create_app(
        {
            "TESTING": True,
            "MEDIA_SERVER_TYPE": "JELLYFIN",
            "JELLYFIN_URL": "http://jellyfin:8096",
            "JELLYFIN_API_KEY": "secret",
            "JELLYFIN_USER_ID": "user-1",
            "JELLYFIN_LIBRARY_ID": "library-1",
        },
        database=scripted_db,
    )
    with app.app_context():
        client = _client()
    assert isinstance(client, JellyfinHTTPClient)
    assert client.config.library_id == "library-1"


def test_sync_records_created_movies(scripted_db, scripted_connection):
    class Client:
        @staticmethod
        def movies():
            return [{
                "plex_rating_key": "42", "title": "Arrival", "release_year": 2016,
                "summary": "Visitors arrive.", "poster_path": "/poster", "background_path": "/art",
                "duration_minutes": 116, "content_rating": "PG-13", "audience_rating": 8.1,
                "critic_rating": 7.9, "genres": ["Drama"], "date_added": 1, "view_count": 0,
                "watched": False, "plex_watch_url": "plex://play/42", "metadata": {},
            }]

    result = sync_movies(scripted_db, Client())
    assert result["movies_created"] == 1
    assert any(query.startswith("INSERT INTO movies") for query, _ in scripted_connection.executed)
    assert any("status = 'success'" in query for query, _ in scripted_connection.executed)


def test_sync_failure_records_safe_health_status(scripted_db, scripted_connection):
    class Client:
        @staticmethod
        def movies():
            raise PlexUnavailable("private upstream detail")

    with pytest.raises(PlexUnavailable):
        sync_movies(scripted_db, Client())
    failure = next(
        params
        for query, params in scripted_connection.executed
        if "status = 'failed'" in query
    )
    assert failure[0] == "Plex synchronization failed."
    assert "private upstream detail" not in str(failure)


def test_image_proxy_streams_without_exposing_token(app, client, monkeypatch, scripted_connection):
    scripted_connection.one_handler = lambda query, params: {"image_path": "/poster/42"}
    with client.session_transaction() as browser_session:
        browser_session["participant_id"] = "p1"
        browser_session["display_name"] = "Ada"
        browser_session["csrf_token"] = "csrf"

    class Upstream:
        headers = {"Content-Type": "image/jpeg"}

        @staticmethod
        def iter_content(chunk_size):
            yield b"poster-bytes"

    class Client:
        @staticmethod
        def image(path):
            assert path == "/poster/42"
            return Upstream()

    monkeypatch.setattr("app.plex.routes._client", lambda: Client())
    response = client.get("/media/poster/movie1")
    assert response.status_code == 200
    assert response.data == b"poster-bytes"
    assert b"token" not in response.data


def test_sync_coordinator_prevents_auto_manual_overlap(
    app, monkeypatch, tmp_path
):
    app.config["MATCHARR_SYNC_LOCK_PATH"] = str(tmp_path / "media-sync.lock")
    coordinator = MediaSyncCoordinator(app)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_sync(*_args, **_kwargs):
        started.set()
        release.wait(timeout=5)
        finished.set()
        return {"status": "success", "movies_received": 0}

    monkeypatch.setattr("app.plex.coordinator.build_client", lambda _config: object())
    monkeypatch.setattr("app.plex.coordinator.sync_movies", blocking_sync)

    assert coordinator.trigger_startup() is True
    assert started.wait(timeout=2)
    with pytest.raises(SyncAlreadyRunning, match="already running"):
        coordinator.run_now()
    release.set()
    assert finished.wait(timeout=2)
    assert coordinator.trigger_startup() is False


def test_file_lock_rejects_a_second_holder(tmp_path):
    path = tmp_path / "shared-media-sync.lock"
    first = _FileLock(path)
    second = _FileLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_only_one_gunicorn_worker_claims_startup_sync(
    app, monkeypatch, tmp_path
):
    app.config["MATCHARR_SYNC_LOCK_PATH"] = str(tmp_path / "gunicorn-sync.lock")
    monkeypatch.setattr(
        "app.plex.coordinator.sys.argv",
        ["gunicorn", "--workers", "2", "run:app"],
    )
    monkeypatch.setattr("app.plex.coordinator.os.getppid", lambda: 4242)
    first = MediaSyncCoordinator(app)
    second = MediaSyncCoordinator(app)
    assert first._claim_gunicorn_startup() is True
    assert second._claim_gunicorn_startup() is False


def test_first_request_launches_startup_sync_without_waiting(
    app, client, monkeypatch
):
    coordinator = app.extensions["media_sync_coordinator"]
    launched = threading.Event()
    release = threading.Event()

    def slow_background_sync():
        launched.set()
        release.wait(timeout=2)

    monkeypatch.setattr(coordinator, "_background_sync", slow_background_sync)
    app.config["TESTING"] = False
    app.config["MATCHARR_AUTO_SYNC"] = True

    response = client.get("/health")

    assert response.status_code == 200
    assert launched.wait(timeout=1)
    assert coordinator._startup_triggered is True
    release.set()


def test_manual_sync_reports_existing_background_refresh(app, client, monkeypatch):
    with client.session_transaction() as browser_session:
        browser_session["participant_id"] = "p1"
        browser_session["display_name"] = "Ada"
        browser_session["csrf_token"] = "csrf"

    def busy():
        raise SyncAlreadyRunning("A media library sync is already running.")

    monkeypatch.setattr(
        app.extensions["media_sync_coordinator"],
        "run_now",
        busy,
    )
    response = client.post(
        "/media/sync",
        data={"csrf_token": "csrf"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert any(
            "already running" in message
            for _category, message in browser_session["_flashes"]
        )


def test_health_exposes_running_sync_details(
    client, scripted_connection
):
    scripted_connection.one_handler = lambda query, _params: (
        {
            "status": "running",
            "movies_received": 0,
            "movies_created": 0,
            "movies_updated": 0,
            "error_message": None,
            "started_at": 123,
            "completed_at": None,
        }
        if "FROM plex_sync_runs" in query
        else None
    )
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["media_last_sync"]["status"] == "running"
    assert response.get_json()["media_last_sync"]["started_at"] == 123
