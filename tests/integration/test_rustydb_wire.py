from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def test_prepared_parameters_and_transactions(database):
    table = "matcharr_wire_" + uuid.uuid4().hex[:12]
    database.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, value TEXT UNIQUE)")
    try:
        row_id = str(uuid.uuid4())
        with database.transaction() as connection:
            connection.execute(
                f"INSERT INTO {table} (id, value) VALUES (%s, %s)",
                (row_id, "bound-value"),
            )
            row = connection.fetch_one(
                f"SELECT value FROM {table} WHERE id = %s", (row_id,)
            )
            assert row["value"] == "bound-value"
        assert database.fetch_one(
            f"SELECT id FROM {table} WHERE value = %s", ("bound-value",)
        )["id"] == row_id
    finally:
        database.execute(f"DROP TABLE {table}")
