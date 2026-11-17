import pytest

from app import create_app
from tests.conftest import csrf


@pytest.mark.parametrize("secret", [None, "development-only-change-me", "generate-a-long-random-production-secret"])
def test_app_rejects_unsafe_signing_keys(secret):
    with pytest.raises(ValueError, match="Set SECRET_KEY"):
        create_app({"SECRET_KEY": secret})


@pytest.mark.parametrize("password", [None, "replace-this", "replace-this-with-a-long-random-password"])
def test_app_rejects_placeholder_database_passwords(password):
    with pytest.raises(ValueError, match="Set RUSTYDB_PASSWORD"):
        create_app({"SECRET_KEY": "a" * 64, "RUSTYDB_PASSWORD": password})


def _identity(client, participant_id="p1", name="Ada"):
    client.get("/")
    with client.session_transaction() as browser_session:
        browser_session["participant_id"] = participant_id
        browser_session["display_name"] = name
        browser_session.setdefault("csrf_token", "test-csrf")


def test_json_mutations_require_csrf(client):
    _identity(client)
    response = client.post(
        "/api/rooms/r1/sessions/s1/swipes",
        json={"movie_id": "m1", "decision": "like", "idempotency_key": "x"},
    )
    assert response.status_code == 400


def test_non_member_cannot_access_room(client):
    _identity(client)
    response = client.get("/rooms/room1")
    assert response.status_code == 403


def test_room_and_session_status_share_completed_session_with_members(
    app, scripted_connection
):
    members = [
        {"participant_id": "p1", "display_name": "Ada", "role": "owner", "is_active": True},
        {"participant_id": "p2", "display_name": "Grace", "role": "member", "is_active": True},
    ]
    selection = {"id": "s1", "status": "active", "started_at": 10, "completed_at": None}
    matches = [{"id": "ma1", "title": "Arrival", "movie_id": "m1"}]
    votes = []

    def one(query, params):
        if "FROM room_members" in query or "FROM session_members sm" in query:
            if params[1] in {"p1", "p2"}:
                return {"id": params[1], "is_active": True, "status": selection["status"]}
        if "FROM selection_sessions" in query:
            if "status = 'active'" not in query or selection["status"] == "active":
                return selection
        if "FROM session_movies" in query:
            return {"total": 3}
        if "FROM swipes" in query:
            return {"total": 2}
        return None

    def all_rows(query, _params):
        if "FROM room_members" in query or "FROM session_members" in query:
            return members
        if "FROM matches" in query:
            return matches
        if "FROM session_finish_votes" in query:
            return votes
        return []

    scripted_connection.one_handler = one
    scripted_connection.all_handler = all_rows
    phones = [app.test_client(), app.test_client()]
    _identity(phones[0], "p1", "Ada")
    _identity(phones[1], "p2", "Grace")
    status_url = "/api/rooms/r1/sessions/s1/status"
    for phone in phones:
        response = phone.get(status_url)
        assert response.headers["Cache-Control"] == "private, no-store"
        assert [match["id"] for match in response.json["matches"]] == ["ma1"]

    matches.append({"id": "ma2", "title": "Moon", "movie_id": "m2"})
    votes.extend(members)
    selection.update(status="completed", completed_at=20)
    for phone in phones:
        status = phone.get(status_url).json
        assert status["session"]["status"] == "completed"
        assert status["progress"]["finish_votes"] == 2
        assert [match["id"] for match in status["matches"]] == ["ma1", "ma2"]
        room = phone.get("/api/rooms/r1/status?session_id=s1").json
        assert room["session"] is None
        assert room["tracked_session"]["status"] == "completed"

    outsider = app.test_client()
    _identity(outsider, "p3", "Lin")
    assert outsider.get(status_url).status_code == 403
    assert outsider.get("/api/rooms/r1/status?session_id=s1").status_code == 403


def test_invite_failures_are_rate_limited(client):
    _identity(client)
    with client.session_transaction() as browser_session:
        token = browser_session["csrf_token"]
    for _ in range(2):
        response = client.post(
            "/rooms/join",
            data={"csrf_token": token, "invite_code": "BADCODE"},
        )
        assert response.status_code == 400
    response = client.post(
        "/rooms/join",
        data={"csrf_token": token, "invite_code": "BADCODE"},
    )
    assert response.status_code == 429


def test_security_headers_are_present(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_quick_join_creates_identity_and_membership(
    client, scripted_connection
):
    scripted_connection.one_handler = lambda query, params: (
        {"id": "room1", "name": "Friday Films", "is_archived": False}
        if "FROM rooms WHERE invite_code" in query
        else None
    )
    response = client.get("/join/abc23456")
    assert response.status_code == 200
    assert b"Friday Films" in response.data
    assert b"Display name" in response.data
    with client.session_transaction() as browser_session:
        token = browser_session["csrf_token"]
    response = client.post(
        "/join/abc23456",
        data={"csrf_token": token, "display_name": "Grace"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/rooms/room1")
    assert any(
        query.startswith("INSERT INTO room_members")
        for query, _ in scripted_connection.executed
    )


def test_invalid_quick_join_link_returns_404(client, scripted_connection):
    scripted_connection.one_handler = lambda query, params: None
    response = client.get("/join/INVALID")
    assert response.status_code == 404
    assert b"Invite unavailable" in response.data


def test_room_member_can_render_private_invite_qr(
    client, app, scripted_connection
):
    _identity(client)
    scripted_connection.one_handler = lambda query, params: (
        {
            "id": "membership1",
            "role": "owner",
            "is_active": True,
            "display_name": "Ada",
            "room_name": "Friday Films",
            "invite_code": "ABC23456",
            "is_archived": False,
            "include_watched": False,
            "owner_participant_id": "p1",
            "default_filters_json": "{}",
        }
        if "FROM room_members rm INNER JOIN rooms r" in query
        else None
    )
    app.config["MATCHARR_PUBLIC_URL"] = "https://watch.example"
    response = client.get("/rooms/room1/invite-qr.svg")
    assert response.status_code == 200
    assert response.content_type == "image/svg+xml"
    assert b"<svg" in response.data
