from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flickr_exporter.exporter import FlickrExporter, album_directory_name, filter_unorganized_photos, sanitize_filename
from flickr_exporter.models import Album, Credentials, Photo


class DummyMetadataWriter:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[Path, Photo]] = []

    def write_metadata(self, photo_path: str | Path, photo: Photo) -> None:
        target = Path(photo_path)
        self.calls.append((target, photo))
        if self.should_fail:
            raise RuntimeError("metadata failed")


class FakeFlickrClient:
    def __init__(self) -> None:
        self.credentials = Credentials(
            api_key="key",
            api_secret="secret",
            oauth_token="token",
            oauth_token_secret="token-secret",
        )
        self.request_timeout = 1.0
        self.verbose = False
        self.album_photos: list[Photo] = []
        self.albums: list[Album] = []
        self.all_photos: list[Photo] = []

    def clone(self) -> "FakeFlickrClient":
        clone = FakeFlickrClient()
        clone.album_photos = list(self.album_photos)
        clone.albums = list(self.albums)
        clone.all_photos = list(self.all_photos)
        return clone

    def get_album_info(self, album_id: str) -> Album:
        return next(album for album in self.albums if album.id == album_id)

    def get_album_photos(self, album_id: str) -> list[Photo]:
        return list(self.album_photos)

    def get_collection_albums(self, collection_id: str):
        return list(self.albums), "Collection"

    def get_all_albums(self) -> list[Album]:
        return list(self.albums)

    def get_all_photos(self) -> list[Photo]:
        return list(self.all_photos)

    def get_photo_info(self, photo_id: str) -> Photo:
        return Photo(
            id=photo_id,
            title="Fetched Title",
            description="Fetched Description",
            tags=["one", "two"],
        )


def make_exporter(tmp_path, *, metadata_writer=None) -> FlickrExporter:
    return FlickrExporter(
        client=FakeFlickrClient(),
        output_dir=str(tmp_path),
        metadata_writer=metadata_writer or DummyMetadataWriter(),
        verbose=False,
        sleeper=lambda _: None,
    )


def test_sanitize_filename_replaces_problem_characters():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"


def test_album_directory_name_uses_date_prefix():
    album = Album(id="1", title="Trip", date_created=datetime(2023, 1, 15))

    assert album_directory_name(album) == "2023-01-15 Trip"


def test_download_album_skips_existing_files_without_fetching_metadata(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path)
    album = Album(
        id="1",
        title="Trip",
        date_created=datetime(2023, 1, 15),
        photos=[Photo(id="photo-1", title="Photo", original_url="https://example.com/photo.jpg", filename="photo.jpg")],
    )
    album_path = tmp_path / "2023-01-15 Trip"
    album_path.mkdir(parents=True)
    existing = album_path / "photo.jpg"
    existing.write_bytes(b"already here")

    monkeypatch.setattr(exporter, "fetch_photo_metadata", lambda photo: pytest.fail("metadata should not be fetched"))
    monkeypatch.setattr(exporter, "download_photo", lambda photo, output_path: pytest.fail("photo should not be downloaded"))

    exporter.download_album(album)

    assert existing.read_bytes() == b"already here"


def test_download_album_removes_photo_when_metadata_write_fails(tmp_path, monkeypatch):
    writer = DummyMetadataWriter(should_fail=True)
    exporter = make_exporter(tmp_path, metadata_writer=writer)
    album = Album(
        id="1",
        title="Trip",
        date_created=datetime(2023, 1, 15),
        photos=[Photo(id="photo-1", title="Photo", original_url="https://example.com/photo.jpg", filename="photo.jpg")],
    )

    monkeypatch.setattr(exporter, "fetch_photo_metadata", lambda photo: None)

    def fake_download(photo, output_path):
        Path(output_path).write_bytes(b"downloaded")

    monkeypatch.setattr(exporter, "download_photo", fake_download)

    with pytest.raises(RuntimeError, match="failed to download 1 photos"):
        exporter.download_album(album)

    assert not (tmp_path / "2023-01-15 Trip" / "photo.jpg").exists()


def test_filter_unorganized_photos_uses_filename_only():
    photos = [
        Photo(id="1", filename="one.jpg"),
        Photo(id="2", filename="two.jpg"),
    ]

    filtered = filter_unorganized_photos(photos, {"two.jpg"})

    assert [photo.filename for photo in filtered] == ["one.jpg"]


def test_export_all_tracks_album_filenames_before_unorganized_step(tmp_path, monkeypatch):
    client = FakeFlickrClient()
    client.albums = [Album(id="album-1", title="Trip", date_created=datetime(2023, 1, 15))]
    client.album_photos = [Photo(id="photo-1", filename="album-photo.jpg", original_url="https://example.com/photo.jpg")]
    exporter = FlickrExporter(
        client=client,
        output_dir=str(tmp_path),
        metadata_writer=DummyMetadataWriter(),
        verbose=False,
        sleeper=lambda _: None,
    )

    monkeypatch.setattr(FlickrExporter, "download_album", lambda self, album: None)

    captured: dict[str, set[str]] = {}

    def fake_download_unorganized(downloaded_files: set[str]) -> None:
        captured["downloaded_files"] = set(downloaded_files)

    monkeypatch.setattr(exporter, "download_unorganized_photos", fake_download_unorganized)

    exporter.export_all_photos()

    assert captured["downloaded_files"] == {"album-photo.jpg"}
