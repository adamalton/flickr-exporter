from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from time import sleep
from typing import Any
from urllib.parse import unquote, urlparse

from requests_oauthlib import OAuth1Session

from flickr_exporter.models import Album, Credentials, Photo

REST_URL = "https://www.flickr.com/services/rest"


class FlickrApiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class FlickrClient:
    credentials: Credentials
    verbose: bool = False
    request_timeout: float = 60.0

    def clone(self) -> "FlickrClient":
        return FlickrClient(
            credentials=Credentials(
                api_key=self.credentials.api_key,
                api_secret=self.credentials.api_secret,
                oauth_token=self.credentials.oauth_token,
                oauth_token_secret=self.credentials.oauth_token_secret,
            ),
            verbose=self.verbose,
            request_timeout=self.request_timeout,
        )

    def get_album_info(self, album_id: str) -> Album:
        payload = self._call("flickr.photosets.getInfo", {"photoset_id": album_id})
        photoset = payload.get("photoset", {})
        return self._album_from_photoset(photoset, fallback_to_now=True)

    def get_album_photos(self, album_id: str) -> list[Photo]:
        photos: list[Photo] = []
        page = 1

        while True:
            payload = self._call(
                "flickr.photosets.getPhotos",
                {
                    "photoset_id": album_id,
                    "page": page,
                    "extras": "original_format,url_o",
                },
            )
            photoset = payload.get("photoset", {})
            for photo_data in _normalize_list(photoset.get("photo")):
                photo = self._photo_from_listing(photo_data)
                if photo.original_url:
                    photos.append(photo)

            if page >= int(photoset.get("pages", page) or page):
                break

            page += 1
            sleep(0.1)

        return photos

    def get_collection_albums(self, collection_id: str) -> tuple[list[Album], str]:
        payload = self._call("flickr.collections.getTree", {"collection_id": collection_id})
        collections = _normalize_list(payload.get("collections", {}).get("collection"))
        albums: list[Album] = []
        collection_name = ""

        for collection in collections:
            if not collection_name:
                collection_name = _extract_content(collection.get("title"))
            for raw_set in _normalize_list(collection.get("set")):
                set_id = str(raw_set.get("id", "") or "")
                if not set_id:
                    continue
                try:
                    album = self.get_album_info(set_id)
                except Exception:
                    album = Album(
                        id=set_id,
                        title=_extract_content(raw_set.get("title")),
                        description=_extract_content(raw_set.get("description")),
                        date_created=datetime.fromtimestamp(0),
                    )
                albums.append(album)

        if not albums:
            raise FlickrApiError(f"no albums found in collection {collection_id}")

        return albums, collection_name

    def get_all_albums(self) -> list[Album]:
        albums: list[Album] = []
        page = 1

        while True:
            payload = self._call("flickr.photosets.getList", {"page": page})
            photosets = payload.get("photosets", {})
            for photoset in _normalize_list(photosets.get("photoset")):
                albums.append(self._album_from_photoset(photoset, fallback_to_now=False))

            if page >= int(photosets.get("pages", page) or page):
                break

            page += 1
            sleep(0.1)

        return albums

    def get_all_photos(self) -> list[Photo]:
        all_photos: list[Photo] = []
        page = 1

        while True:
            payload = self._call(
                "flickr.people.getPhotos",
                {
                    "user_id": "me",
                    "extras": "original_format,url_o",
                    "per_page": 500,
                    "page": page,
                },
            )
            photos = payload.get("photos", {})
            page_count = int(photos.get("pages", page) or page)
            page_items = _normalize_list(photos.get("photo"))
            print(f"Fetching page {page}/{page_count}: Got {len(page_items)} photos")

            for photo_data in page_items:
                photo = self._photo_from_listing(photo_data)
                if photo.original_url:
                    all_photos.append(photo)

            if page >= page_count:
                break

            page += 1
            sleep(0.1)

        print(f"Found {len(all_photos)} total photos in your account")
        return all_photos

    def get_photo_info(self, photo_id: str) -> Photo:
        max_retries = 5
        base_delay = 2.0

        for attempt in range(max_retries):
            try:
                payload = self._call("flickr.photos.getInfo", {"photo_id": photo_id})
            except FlickrApiError as error:
                message = str(error).lower()
                is_rate_limited = error.status_code == 429 or "rate limit" in message or "too many requests" in message
                if is_rate_limited and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    if self.verbose:
                        print(
                            f"Rate limited getting photo info for {photo_id}, retrying in "
                            f"{delay:.0f}s (attempt {attempt + 1}/{max_retries})"
                        )
                    sleep(delay)
                    continue
                raise FlickrApiError(f"failed to get photo info for {photo_id} after {max_retries} attempts: {error}")

            photo_data = payload.get("photo", {})
            tags = [str(tag.get("raw", "") or "") for tag in _normalize_list(photo_data.get("tags", {}).get("tag"))]

            date_taken = None
            taken = str(photo_data.get("dates", {}).get("taken", "") or "")
            if taken:
                try:
                    date_taken = datetime.strptime(taken, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    date_taken = None

            return Photo(
                id=photo_id,
                title=_extract_content(photo_data.get("title")),
                description=_extract_content(photo_data.get("description")),
                tags=tags,
                date_taken=date_taken,
            )

        raise FlickrApiError(f"failed to get photo info for {photo_id} after {max_retries} retry attempts")

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        session = OAuth1Session(
            self.credentials.api_key,
            client_secret=self.credentials.api_secret,
            resource_owner_key=self.credentials.oauth_token,
            resource_owner_secret=self.credentials.oauth_token_secret,
        )
        request_params: dict[str, Any] = {
            "method": method,
            "format": "json",
            "nojsoncallback": 1,
            **params,
        }
        response = session.get(REST_URL, params=request_params, timeout=self.request_timeout)

        if response.status_code == 429:
            raise FlickrApiError(f"HTTP 429: {response.reason}", status_code=429)
        if response.status_code >= 400:
            raise FlickrApiError(
                f"HTTP {response.status_code}: {response.reason}",
                status_code=response.status_code,
            )

        payload = response.json()
        if payload.get("stat") == "fail":
            raise FlickrApiError(
                str(payload.get("message", "Flickr API request failed")),
                code=_safe_int(payload.get("code")),
                status_code=response.status_code,
            )
        return payload

    def _album_from_photoset(self, data: dict[str, Any], *, fallback_to_now: bool) -> Album:
        date_created = _parse_unix_timestamp(data.get("date_create"))
        if date_created is None:
            date_created = datetime.now() if fallback_to_now else datetime.fromtimestamp(0)

        return Album(
            id=str(data.get("id", "") or ""),
            title=_extract_content(data.get("title")),
            description=_extract_content(data.get("description")),
            date_created=date_created,
        )

    def _photo_from_listing(self, data: dict[str, Any]) -> Photo:
        original_url = str(data.get("url_o", "") or "")
        return Photo(
            id=str(data.get("id", "") or ""),
            title=str(data.get("title", "") or ""),
            original_url=original_url,
            filename=_filename_from_url(original_url),
        )


def _extract_content(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("_content", "") or "")
    return str(value or "")


def _normalize_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _parse_unix_timestamp(value: Any) -> datetime | None:
    try:
        timestamp = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _filename_from_url(url: str) -> str:
    if not url:
        return ""
    return unquote(PurePosixPath(urlparse(url).path).name)
