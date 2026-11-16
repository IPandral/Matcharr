from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import requests


class PlexUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PlexConfig:
    url: str
    token: str
    library_id: str
    library_name: str
    timeout: float = 10


class PlexHTTPClient:
    provider_name = "Plex"
    source_type = "plex"

    def __init__(self, config: PlexConfig, http=requests):
        self.config = config
        self.http = http

    @property
    def _headers(self):
        return {"X-Plex-Token": self.config.token, "Accept": "application/xml"}

    def _get_xml(self, path: str, params: dict | None = None):
        if not self.config.url or not self.config.token:
            raise PlexUnavailable("Plex is not configured.")
        try:
            response = self.http.get(
                f"{self.config.url}{path}",
                headers=self._headers,
                params=params,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            return ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as error:
            raise PlexUnavailable("Plex could not be reached or returned invalid metadata.") from error

    def ping(self) -> bool:
        self._get_xml("/")
        return True

    def movies(self) -> list[dict]:
        root = self._get_xml(
            f"/library/sections/{quote(str(self.config.library_id), safe='')}/all",
            {"type": "1", "includeGuids": "1"},
        )
        movies = []
        for video in root.findall("Video"):
            rating_key = video.get("ratingKey")
            if not rating_key:
                continue
            duration_ms = int(video.get("duration") or 0)
            metadata = {
                "source": "plex",
                "source_id": rating_key,
                "guid": video.get("guid"),
                "studio": video.get("studio"),
                "tagline": video.get("tagline"),
                "originally_available_at": video.get("originallyAvailableAt"),
            }
            movies.append(
                {
                    "plex_rating_key": rating_key,
                    "title": video.get("title") or "Untitled",
                    "release_year": int(video.get("year")) if video.get("year") else None,
                    "summary": video.get("summary") or "",
                    "poster_path": video.get("thumb"),
                    "background_path": video.get("art"),
                    "duration_minutes": round(duration_ms / 60000) if duration_ms else None,
                    "content_rating": video.get("contentRating"),
                    "audience_rating": _float(video.get("audienceRating") or video.get("rating")),
                    "critic_rating": _float(video.get("rating")),
                    "genres": [node.get("tag") for node in video.findall("Genre") if node.get("tag")],
                    "date_added": int(video.get("addedAt") or 0) or None,
                    "view_count": int(video.get("viewCount") or 0),
                    "watched": int(video.get("viewCount") or 0) > 0,
                    "plex_watch_url": f"plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F{quote(rating_key, safe='')}",
                    "metadata": metadata,
                }
            )
        return movies

    def image(self, path: str):
        if not path.startswith("/"):
            raise PlexUnavailable("Invalid Plex image path.")
        try:
            response = self.http.get(
                f"{self.config.url}{path}",
                headers={"X-Plex-Token": self.config.token},
                timeout=self.config.timeout,
                stream=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise PlexUnavailable("Plex image is unavailable.") from error


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
