-- RustyDB v0.3.0-beta compatible schema. IDs are Python-generated UUID TEXT.
CREATE TABLE participants (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE TABLE movies (
    id TEXT PRIMARY KEY,
    plex_rating_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    release_year INTEGER,
    summary TEXT,
    poster_path TEXT,
    background_path TEXT,
    duration_minutes INTEGER,
    content_rating TEXT,
    audience_rating FLOAT,
    critic_rating FLOAT,
    genres_json TEXT,
    date_added INTEGER,
    view_count INTEGER,
    watched BOOLEAN NOT NULL,
    plex_watch_url TEXT,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE rooms (
    id TEXT PRIMARY KEY,
    owner_participant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    is_archived BOOLEAN NOT NULL,
    include_watched BOOLEAN NOT NULL,
    default_filters_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (owner_participant_id) REFERENCES participants(id)
);

CREATE TABLE room_members (
    id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    is_active BOOLEAN NOT NULL,
    joined_at INTEGER NOT NULL,
    left_at INTEGER,
    UNIQUE (room_id, participant_id),
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id)
);

CREATE TABLE selection_sessions (
    id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    created_by_participant_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'cancelled')),
    filters_json TEXT,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (created_by_participant_id) REFERENCES participants(id)
);

CREATE TABLE session_members (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    room_member_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_eligible BOOLEAN NOT NULL,
    added_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, participant_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (room_member_id) REFERENCES room_members(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id)
);

CREATE TABLE session_movies (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    movie_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_feed TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, movie_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (movie_id) REFERENCES movies(id)
);

CREATE TABLE swipes (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    movie_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('like', 'skip')),
    idempotency_key TEXT UNIQUE,
    created_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, participant_id, movie_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id),
    FOREIGN KEY (movie_id) REFERENCES movies(id)
);

CREATE TABLE matches (
    id TEXT PRIMARY KEY,
    selection_session_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    movie_id TEXT NOT NULL,
    status TEXT NOT NULL,
    matched_at INTEGER NOT NULL,
    UNIQUE (selection_session_id, movie_id),
    FOREIGN KEY (selection_session_id) REFERENCES selection_sessions(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (movie_id) REFERENCES movies(id)
);

CREATE TABLE match_members (
    id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    liked_at INTEGER NOT NULL,
    UNIQUE (match_id, participant_id),
    FOREIGN KEY (match_id) REFERENCES matches(id),
    FOREIGN KEY (participant_id) REFERENCES participants(id)
);

CREATE TABLE plex_sync_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    movies_received INTEGER NOT NULL,
    movies_created INTEGER NOT NULL,
    movies_updated INTEGER NOT NULL,
    error_message TEXT,
    started_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE INDEX movies_date_added_idx ON movies(date_added);
CREATE INDEX room_members_active_idx ON room_members(room_id, is_active);
CREATE INDEX selection_sessions_room_status_idx ON selection_sessions(room_id, status);
CREATE INDEX session_movies_session_position_idx ON session_movies(selection_session_id, position);
CREATE INDEX swipes_session_participant_movie_idx ON swipes(selection_session_id, participant_id, movie_id);
CREATE INDEX matches_room_session_idx ON matches(room_id, selection_session_id);

