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
