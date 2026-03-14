from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Credentials:
    api_key: str = ""
    api_secret: str = ""
    oauth_token: str = ""
    oauth_token_secret: str = ""


@dataclass(slots=True)
class Photo:
    id: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    original_url: str = ""
    filename: str = ""
    date_taken: datetime | None = None


@dataclass(slots=True)
class Album:
    id: str
    title: str = ""
    description: str = ""
    date_created: datetime | None = None
    photos: list[Photo] = field(default_factory=list)
