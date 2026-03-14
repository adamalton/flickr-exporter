from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flickr_exporter.exporter import (
    FlickrExporter,
    PermanentDownloadError,
    RetryableDownloadError,
    album_directory_name,
    filter_unorganized_photos,
    photo_date_directory_name,
    photo_output_filename,
    sanitize_filename,
)
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


def test_photo_date_directory_name_uses_year_month():
    photo = Photo(id="1", date_taken=datetime(2026, 2, 10, 12, 0, 0))

    assert photo_date_directory_name(photo) == "2026-02"


def test_photo_date_directory_name_handles_missing_date():
    assert photo_date_directory_name(Photo(id="1")) == "Unknown Date"


def test_photo_output_filename_falls_back_to_photo_id():
    assert photo_output_filename(Photo(id="photo-1", filename="")) == "photo-1"


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


def test_download_photo_to_directory_skips_existing_photo_using_fallback_filename(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path)
    target_dir = tmp_path / "2026-02"
    target_dir.mkdir(parents=True)
    existing = target_dir / "photo-1"
    existing.write_bytes(b"already here")
    photo = Photo(id="photo-1", filename="", original_url="https://example.com/photo")

    monkeypatch.setattr(exporter, "download_photo", lambda photo, output_path: pytest.fail("photo should not be downloaded"))

    assert exporter._download_photo_to_directory(1, photo, target_dir) is None
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


def test_export_all_photos_by_date_uses_year_month_directories(tmp_path, monkeypatch):
    writer = DummyMetadataWriter()
    client = FakeFlickrClient()
    client.all_photos = [Photo(id="photo-1", filename="photo.jpg", original_url="https://example.com/photo.jpg")]
    exporter = FlickrExporter(
        client=client,
        output_dir=str(tmp_path),
        metadata_writer=writer,
        verbose=False,
        sleeper=lambda _: None,
    )

    def fake_fetch(photo: Photo) -> None:
        photo.date_taken = datetime(2026, 2, 10, 12, 0, 0)

    def fake_download(photo: Photo, output_path: str | Path) -> None:
        Path(output_path).write_bytes(b"downloaded")

    monkeypatch.setattr(FlickrExporter, "fetch_photo_metadata", lambda self, photo: fake_fetch(photo))
    monkeypatch.setattr(FlickrExporter, "download_photo", lambda self, photo, output_path: fake_download(photo, output_path))

    exporter.export_all_photos_by_date()

    expected_path = tmp_path / "2026-02" / "photo.jpg"
    assert expected_path.exists()
    assert writer.calls[0][0] == expected_path


def test_export_all_photos_by_date_uses_photo_id_when_filename_missing(tmp_path, monkeypatch):
    writer = DummyMetadataWriter()
    client = FakeFlickrClient()
    client.all_photos = [Photo(id="photo-1", filename="", original_url="https://example.com/photo")]
    exporter = FlickrExporter(
        client=client,
        output_dir=str(tmp_path),
        metadata_writer=writer,
        verbose=False,
        sleeper=lambda _: None,
    )

    def fake_fetch(photo: Photo) -> None:
        photo.date_taken = datetime(2026, 2, 10, 12, 0, 0)

    def fake_download(photo: Photo, output_path: str | Path) -> None:
        Path(output_path).write_bytes(b"downloaded")

    monkeypatch.setattr(FlickrExporter, "fetch_photo_metadata", lambda self, photo: fake_fetch(photo))
    monkeypatch.setattr(FlickrExporter, "download_photo", lambda self, photo, output_path: fake_download(photo, output_path))

    exporter.export_all_photos_by_date()

    expected_path = tmp_path / "2026-02" / "photo-1"
    assert expected_path.exists()
    assert writer.calls[0][0] == expected_path


def test_download_photo_attempt_aborts_after_hard_timeout(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path)
    output_path = tmp_path / "photo.jpg"
    monotonic_values = iter([0.0, 301.0])

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_content(self, chunk_size: int):
            yield b"partial-data"

    monkeypatch.setattr("flickr_exporter.exporter.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("flickr_exporter.exporter.requests.get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError, match="hard timeout"):
        exporter._download_photo_attempt("https://example.com/photo.jpg", output_path)


def test_download_photo_retries_transient_failures(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path)
    photo = Photo(id="photo-1", filename="photo.jpg", original_url="https://example.com/photo.jpg")
    attempts: list[int] = []
    sleeps: list[float] = []

    def fake_attempt(url: str, output_path: Path) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RetryableDownloadError("download timed out")
        output_path.write_bytes(b"downloaded")

    monkeypatch.setattr(exporter, "_download_photo_attempt", fake_attempt)
    monkeypatch.setattr(exporter, "_sleep", lambda seconds: sleeps.append(seconds))

    exporter.download_photo(photo, tmp_path / "photo.jpg")

    assert attempts == [1, 2, 3]
    assert sleeps == [5.0, 10.0]


def test_download_photo_does_not_retry_permanent_failures(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path)
    photo = Photo(id="photo-1", filename="photo.jpg", original_url="https://example.com/photo.jpg")
    attempts: list[int] = []

    def fake_attempt(url: str, output_path: Path) -> None:
        attempts.append(len(attempts) + 1)
        raise PermanentDownloadError("HTTP 404: Not Found")

    monkeypatch.setattr(exporter, "_download_photo_attempt", fake_attempt)

    with pytest.raises(PermanentDownloadError, match="HTTP 404"):
        exporter.download_photo(photo, tmp_path / "photo.jpg")

    assert attempts == [1]
