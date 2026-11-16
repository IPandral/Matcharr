from app.identity.services import change_identity, clean_display_name, save_identity


def test_valid_display_name_is_trimmed():
    assert clean_display_name("  Ada  ") == "Ada"


def test_empty_and_long_display_names_are_rejected():
    for value in ("", "   ", "x" * 41):
        try:
            clean_display_name(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value!r} should be rejected")


def test_participant_identity_is_created_and_reused(scripted_db, scripted_connection):
    participant_id = save_identity(scripted_db, None, "Ada")
    insert = next(query for query in scripted_connection.executed if query[0].startswith("INSERT"))
    assert participant_id in insert[1]

    scripted_connection.one_handler = lambda query, params: {"id": participant_id}
    assert save_identity(scripted_db, participant_id, "Ada Lovelace") == participant_id
    assert any(query.startswith("UPDATE participants") for query, _ in scripted_connection.executed)


def test_changing_display_name_updates_participant_and_active_memberships(
    scripted_db, scripted_connection
):
    scripted_connection.all_handler = lambda query, params: (
        [{"room_id": "room1", "id": "membership1"}]
        if "SELECT room_id, id" in query
        else [{"participant_id": "p1", "display_name": "Ada"}]
    )
    assert change_identity(scripted_db, "p1", "Ada Prime") == "Ada Prime"
    statements = [query for query, _ in scripted_connection.executed]
    assert any(query.startswith("UPDATE participants") for query in statements)
    assert any(query.startswith("UPDATE room_members") for query in statements)


def test_name_route_persists_signed_anonymous_identity(client):
    from tests.conftest import csrf

    token = csrf(client)
    response = client.post(
        "/name",
        data={"csrf_token": token, "display_name": "Ada"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert browser_session["participant_id"]
        assert browser_session["display_name"] == "Ada"


def test_name_route_rejects_invalid_names(client):
    from tests.conftest import csrf

    token = csrf(client)
    response = client.post(
        "/name", data={"csrf_token": token, "display_name": " " * 4}
    )
    assert response.status_code == 400
