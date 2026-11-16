CREATE TABLE session_member_state (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    current_movie_id TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, participant_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id),
    FOREIGN KEY (current_movie_id) REFERENCES movies(id)
);

CREATE TABLE session_finish_votes (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, participant_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id)
);

CREATE INDEX session_member_state_lookup_idx
    ON session_member_state(selection_session_id, participant_id);
CREATE INDEX session_finish_votes_lookup_idx
    ON session_finish_votes(selection_session_id, participant_id);

