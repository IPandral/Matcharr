from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import requests


class JellyfinUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class JellyfinConfig:
    url: str
    api_key: str
    user_id: str
    library_id: str = ""
    library_name: str = "Movies"
    timeout: float = 10


class JellyfinHTTPClient:
    provider_name = "Jellyfin"
    source_type = "jellyfin"

    def __init__(self, config: JellyfinConfig, http=requests):
        self.config = config
        self.http = http

    @property
    def _headers(self):
        return {
            "Authorization": f'MediaBrowser Token="{quote(self.config.api_key, safe="")}"',
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None, stream: bool = False):
        if not self.config.url or not self.config.api_key or not self.config.user_id:
            raise JellyfinUnavailable(
                "Jellyfin URL, API key, and user ID must be configured."
            )
        try:
            response = self.http.get(
                f"{self.config.url}{path}",
                headers=self._headers,
                params=params,
                timeout=self.config.timeout,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise JellyfinUnavailable("Jellyfin could not be reached.") from error

    def _library_id(self) -> str:
        if self.config.library_id:
            return self.config.library_id
        try:
            payload = self._get(
                f"/Users/{quote(self.config.user_id, safe='')}/Views"
            ).json()
        except (TypeError, ValueError) as error:
            raise JellyfinUnavailable(
                "Jellyfin returned invalid library metadata."
            ) from error
        wanted = self.config.library_name.casefold()
        for item in payload.get("Items", []):
            if str(item.get("Name", "")).casefold() == wanted and item.get("Id"):
                return item["Id"]
        raise JellyfinUnavailable(
            f'Jellyfin library "{self.config.library_name}" was not found.'
        )

    def ping(self) -> bool:
        self._get("/System/Info/Public")
        return True

    def movies(self) -> list[dict]:
        fields = "Overview,Genres,DateCreated,ProviderIds,Taglines,Studios"
        try:
            payload = self._get(
                f"/Users/{quote(self.config.user_id, safe='')}/Items",
                {
                    "ParentId": self._library_id(),
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie",
                    "Fields": fields,
                    "EnableImages": "true",
                    "EnableUserData": "true",
                },
            ).json()
        except (TypeError, ValueError) as error:
            raise JellyfinUnavailable("Jellyfin returned invalid movie metadata.") from error

        movies = []
        for item in payload.get("Items", []):
            item_id = item.get("Id")
            if not item_id:
                continue
            user_data = item.get("UserData") or {}
            play_count = int(user_data.get("PlayCount") or 0)
            runtime_ticks = int(item.get("RunTimeTicks") or 0)
            backdrop_tags = item.get("BackdropImageTags") or []
            metadata = {
                "source": "jellyfin",
                "source_id": item_id,
                "premiere_date": item.get("PremiereDate"),
                "date_created": item.get("DateCreated"),
                "provider_ids": item.get("ProviderIds") or {},
                "taglines": item.get("Taglines") or [],
                "studios": item.get("Studios") or [],
            }
            movies.append(
                {
                    "plex_rating_key": f"jellyfin:{item_id}",
                    "title": item.get("Name") or "Untitled",
                    "release_year": _integer(item.get("ProductionYear")),
                    "summary": item.get("Overview") or "",
                    "poster_path": (
                        f"/Items/{quote(item_id, safe='')}/Images/Primary"
                        if (item.get("ImageTags") or {}).get("Primary")
                        else None
                    ),
                    "background_path": (
                        f"/Items/{quote(item_id, safe='')}/Images/Backdrop/0"
                        if backdrop_tags
                        else None
                    ),
                    "duration_minutes": (
                        round(runtime_ticks / 600_000_000) if runtime_ticks else None
                    ),
                    "content_rating": item.get("OfficialRating"),
                    "audience_rating": _float(item.get("CommunityRating")),
                    "critic_rating": _float(item.get("CriticRating")),
                    "genres": item.get("Genres") or [],
                    "date_added": _timestamp(item.get("DateCreated")),
                    "view_count": play_count,
                    "watched": bool(user_data.get("Played")) or play_count > 0,
                    "plex_watch_url": (
                        f"{self.config.url}/web/#/details?id={quote(item_id, safe='')}"
                    ),
                    "metadata": metadata,
                }
            )
        return movies

    def image(self, path: str):
        if not path.startswith("/Items/") or "/Images/" not in path:
            raise JellyfinUnavailable("Invalid Jellyfin image path.")
        return self._get(path, stream=True)


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None
