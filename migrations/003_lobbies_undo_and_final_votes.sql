CREATE TABLE room_lobbies (
    id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL UNIQUE,
    created_by_participant_id TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (created_by_participant_id) REFERENCES participants(id)
);

CREATE TABLE lobby_members (
    id TEXT PRIMARY KEY,
    lobby_id TEXT NOT NULL,
    room_member_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_ready BOOLEAN NOT NULL,
    added_at INTEGER NOT NULL,
    UNIQUE (lobby_id, participant_id),
    FOREIGN KEY (lobby_id) REFERENCES room_lobbies(id),
    FOREIGN KEY (room_member_id) REFERENCES room_members(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id)
);

CREATE TABLE lobby_suggestions (
    id TEXT PRIMARY KEY,
    lobby_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    movie_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (lobby_id, participant_id, movie_id),
    FOREIGN KEY (lobby_id) REFERENCES room_lobbies(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id),
    FOREIGN KEY (movie_id) REFERENCES movies(id)
);

CREATE TABLE session_final_votes (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    match_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, participant_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id),
    FOREIGN KEY (match_id) REFERENCES matches(id)
);

CREATE INDEX lobby_members_ready_idx ON lobby_members(lobby_id, is_ready);
CREATE INDEX lobby_suggestions_lookup_idx ON lobby_suggestions(lobby_id, participant_id);
CREATE INDEX session_final_votes_lookup_idx ON session_final_votes(selection_session_id, match_id);
