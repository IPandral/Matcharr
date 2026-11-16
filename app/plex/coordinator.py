from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from pathlib import Path

from app.jellyfin.client import JellyfinConfig, JellyfinHTTPClient

from .client import PlexConfig, PlexHTTPClient
from .sync import sync_movies


class SyncAlreadyRunning(RuntimeError):
    pass


def build_client(config):
    server_type = config["MEDIA_SERVER_TYPE"]
    if server_type == "jellyfin":
        return JellyfinHTTPClient(
            JellyfinConfig(
                url=config["JELLYFIN_URL"],
                api_key=config["JELLYFIN_API_KEY"],
                user_id=config["JELLYFIN_USER_ID"],
                library_id=config["JELLYFIN_LIBRARY_ID"],
                library_name=config["JELLYFIN_LIBRARY_NAME"],
                timeout=config["JELLYFIN_TIMEOUT"],
            )
        )
    if server_type != "plex":
        raise ValueError("MEDIA_SERVER_TYPE must be either 'plex' or 'jellyfin'.")
    return PlexHTTPClient(
        PlexConfig(
            url=config["PLEX_URL"],
            token=config["PLEX_TOKEN"],
            library_id=config["PLEX_LIBRARY_ID"],
            library_name=config["PLEX_LIBRARY_NAME"],
            timeout=config["PLEX_TIMEOUT"],
        )
    )


class MediaSyncCoordinator:
    def __init__(self, app):
        self.app = app
        self._thread_lock = threading.Lock()
        self._startup_guard = threading.Lock()
        self._startup_triggered = False
        self._lock_path = Path(
            app.config.get("MATCHARR_SYNC_LOCK_PATH") or _default_lock_path(app.config)
        )

    def trigger_startup(self) -> bool:
        with self._startup_guard:
            if self._startup_triggered:
                return False
            self._startup_triggered = True
        if not self._claim_gunicorn_startup():
            return False
        thread = threading.Thread(
            target=self._background_sync,
            name="matcharr-media-startup-sync",
            daemon=True,
        )
        thread.start()
        return True

    def run_now(self) -> dict:
        if not self._thread_lock.acquire(blocking=False):
            raise SyncAlreadyRunning("A media library sync is already running.")
        file_lock = _FileLock(self._lock_path)
        try:
            if not file_lock.acquire():
                raise SyncAlreadyRunning("A media library sync is already running.")
            with self.app.app_context():
                return sync_movies(
                    self.app.extensions["matcharr_db"],
                    build_client(self.app.config),
                    self.app.config["MATCHARR_TRANSACTION_RETRIES"],
                )
        finally:
            file_lock.release()
            self._thread_lock.release()

    def _background_sync(self) -> None:
        try:
            self.run_now()
        except SyncAlreadyRunning:
            return
        except Exception as error:
            self.app.logger.warning(
                "Background media library sync failed (%s).",
                type(error).__name__,
            )

    def _claim_gunicorn_startup(self) -> bool:
        if not any("gunicorn" in argument.casefold() for argument in sys.argv):
            return True
        marker = self._lock_path.with_name(
            f"{self._lock_path.name}.startup-{os.getppid()}"
        )
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        else:
            os.close(descriptor)
            return True


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.handle.tell() == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _default_lock_path(config) -> str:
    media_url = (
        config.get("JELLYFIN_URL")
        if config.get("MEDIA_SERVER_TYPE") == "jellyfin"
        else config.get("PLEX_URL")
    )
    scope = hashlib.sha256(
        (
            f"{config.get('RUSTYDB_HOST')}:{config.get('RUSTYDB_WIRE_PORT')}|"
            f"{config.get('MEDIA_SERVER_TYPE')}|{media_url}"
        ).encode("utf-8")
    ).hexdigest()[:16]
    return str(Path(tempfile.gettempdir()) / f"matcharr-media-sync-{scope}.lock")
