import os


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    MAX_CONTENT_LENGTH = int(os.getenv("MATCHARR_MAX_REQUEST_BYTES", str(1024 * 1024)))

    RUSTYDB_HOST = os.getenv("RUSTYDB_HOST", "rustydb")
    RUSTYDB_WIRE_PORT = int(os.getenv("RUSTYDB_WIRE_PORT", "3307"))
    RUSTYDB_USERNAME = os.getenv("RUSTYDB_USERNAME", "matcharr")
    RUSTYDB_PASSWORD = os.getenv("RUSTYDB_PASSWORD")
    RUSTYDB_CONNECT_TIMEOUT = int(os.getenv("RUSTYDB_CONNECT_TIMEOUT", "5"))
    MATCHARR_TRANSACTION_RETRIES = int(os.getenv("MATCHARR_TRANSACTION_RETRIES", "3"))

    MEDIA_SERVER_TYPE = os.getenv("MEDIA_SERVER_TYPE", "plex").strip().lower()
    PLEX_URL = os.getenv("PLEX_URL", "").rstrip("/")
    PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
    PLEX_LIBRARY_ID = os.getenv("PLEX_LIBRARY_ID", "1")
    PLEX_LIBRARY_NAME = os.getenv("PLEX_LIBRARY_NAME", "Movies")
    PLEX_TIMEOUT = float(os.getenv("PLEX_TIMEOUT", "10"))
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
    JELLYFIN_USER_ID = os.getenv("JELLYFIN_USER_ID", "")
    JELLYFIN_LIBRARY_ID = os.getenv("JELLYFIN_LIBRARY_ID", "")
    JELLYFIN_LIBRARY_NAME = os.getenv("JELLYFIN_LIBRARY_NAME", "Movies")
    JELLYFIN_TIMEOUT = float(os.getenv("JELLYFIN_TIMEOUT", "10"))
    MATCHARR_AUTO_SYNC = _bool("MATCHARR_AUTO_SYNC", True)
    MATCHARR_SYNC_LOCK_PATH = os.getenv("MATCHARR_SYNC_LOCK_PATH", "")

    MATCHARR_INVITE_CODE_LENGTH = int(os.getenv("MATCHARR_INVITE_CODE_LENGTH", "8"))
    MATCHARR_SWIPE_DISTANCE_THRESHOLD = int(
        os.getenv("MATCHARR_SWIPE_DISTANCE_THRESHOLD", "100")
    )
    MATCHARR_SWIPE_VELOCITY_THRESHOLD = float(
        os.getenv("MATCHARR_SWIPE_VELOCITY_THRESHOLD", "0.5")
    )
    MATCHARR_INVITE_ATTEMPTS = int(os.getenv("MATCHARR_INVITE_ATTEMPTS", "8"))
    MATCHARR_INVITE_WINDOW_SECONDS = int(
        os.getenv("MATCHARR_INVITE_WINDOW_SECONDS", "300")
    )
    MATCHARR_POLL_INTERVAL_MS = int(os.getenv("MATCHARR_POLL_INTERVAL_MS", "750"))
    MATCHARR_PUBLIC_URL = os.getenv("MATCHARR_PUBLIC_URL", "").rstrip("/")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("MATCHARR_SECURE_COOKIES", True)


class TestConfig(Config):
    TESTING = True
    SESSION_COOKIE_SECURE = False
