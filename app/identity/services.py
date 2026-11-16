from __future__ import annotations

import time
import uuid

from app.database.transaction import run_transaction

MAX_DISPLAY_NAME_LENGTH = 40


def clean_display_name(value: str | None) -> str:
    name = (value or "").strip()
    if not name:
        raise ValueError("Enter a display name.")
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(f"Display names must be {MAX_DISPLAY_NAME_LENGTH} characters or fewer.")
    return name


def save_identity(database, participant_id: str | None, display_name: str, retries: int = 3) -> str:
    name = clean_display_name(display_name)
    participant_id = participant_id or str(uuid.uuid4())
    now = int(time.time())

    def operation(connection):
        existing = connection.fetch_one(
            "SELECT id FROM participants WHERE id = %s", (participant_id,)
        )
        if existing:
            connection.execute(
                "UPDATE participants SET display_name = %s, last_seen_at = %s WHERE id = %s",
                (name, now, participant_id),
            )
        else:
            connection.execute(
                "INSERT INTO participants "
                "(id, display_name, created_at, last_seen_at) VALUES (%s, %s, %s, %s)",
                (participant_id, name, now, now),
            )
        return participant_id

    return run_transaction(database, operation, retries)


def change_identity(database, participant_id: str, display_name: str, retries: int = 3) -> str:
    name = clean_display_name(display_name)
    now = int(time.time())

    def operation(connection):
        memberships = connection.fetch_all(
            "SELECT room_id, id FROM room_members "
            "WHERE participant_id = %s AND is_active = TRUE",
            (participant_id,),
        )
        for membership in memberships:
            active_names = connection.fetch_all(
                "SELECT participant_id, display_name FROM room_members "
                "WHERE room_id = %s AND is_active = TRUE",
                (membership["room_id"],),
            )
            if any(
                row["participant_id"] != participant_id
                and row["display_name"].casefold() == name.casefold()
                for row in active_names
            ):
                raise ValueError(f'"{name}" is already used in one of your active rooms.')
        connection.execute(
            "UPDATE participants SET display_name = %s, last_seen_at = %s WHERE id = %s",
            (name, now, participant_id),
        )
        connection.execute(
            "UPDATE room_members SET display_name = %s "
            "WHERE participant_id = %s AND is_active = TRUE",
            (name, participant_id),
        )
        return name

    return run_transaction(database, operation, retries)

