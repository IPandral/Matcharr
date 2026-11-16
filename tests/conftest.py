from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import create_app


class ScriptedConnection:
    def __init__(self, one=None, all_rows=None):
        self.one_handler = one or (lambda _query, _params: None)
        self.all_handler = all_rows or (lambda _query, _params: [])
        self.executed = []
        self.queries = []

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))
        return 1

    def fetch_one(self, query, parameters=None):
        self.queries.append((query, parameters))
        return self.one_handler(query, parameters)

    def fetch_all(self, query, parameters=None):
        self.queries.append((query, parameters))
        return self.all_handler(query, parameters)


class ScriptedDB:
    def __init__(self, connection=None):
        self.connection_object = connection or ScriptedConnection()

    @contextmanager
    def transaction(self):
        yield self.connection_object

    def execute(self, query, parameters=None):
        return self.connection_object.execute(query, parameters)

    def fetch_one(self, query, parameters=None):
        return self.connection_object.fetch_one(query, parameters)

    def fetch_all(self, query, parameters=None):
        return self.connection_object.fetch_all(query, parameters)

    def ping(self):
        return True


@pytest.fixture
def scripted_connection():
    return ScriptedConnection()


@pytest.fixture
def scripted_db(scripted_connection):
    return ScriptedDB(scripted_connection)


@pytest.fixture
def app(scripted_db):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SESSION_COOKIE_SECURE": False,
            "MATCHARR_INVITE_ATTEMPTS": 2,
        },
        database=scripted_db,
    )


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client):
    client.get("/")
    with client.session_transaction() as browser_session:
        return browser_session["csrf_token"]
