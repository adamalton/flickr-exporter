from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Callable, Protocol

import requests

from flickr_exporter.flickr_api import FlickrClient
from flickr_exporter.models import Album, Photo

DEFAULT_WORKERS = 4
MIN_HARD_DOWNLOAD_TIMEOUT_PER_PHOTO_SECONDS = 10.0
MAX_DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_RETRY_BASE_DELAY_SECONDS = 5.0


class MetadataWriterProtocol(Protocol):
    def write_metadata(self, photo_path: str | Path, photo: Photo) -> None: ...


class RetryableDownloadError(RuntimeError):
    pass


class PermanentDownloadError(RuntimeError):
    pass


class FlickrExporter:
    def __init__(
        self,
        client: FlickrClient,
        output_dir: str,
        metadata_writer: MetadataWriterProtocol,
        *,
        verbose: bool = False,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.client = client
        self.output_dir = Path(output_dir)
        self.metadata_writer = metadata_writer
        self.verbose = verbose
        self._sleep = sleeper

    def clone(self) -> "FlickrExporter":
        return type(self)(
            client=self.client.clone(),
            output_dir=str(self.output_dir),
            metadata_writer=self.metadata_writer,
            verbose=self.verbose,
            sleeper=self._sleep,
        )

    def export_album(self, album_id: str) -> None:
        print(f"Exporting album {album_id}...")
        album = self.client.get_album_info(album_id)
        album.photos = self.client.get_album_photos(album_id)
        self.download_album(album)

    def export_collection(self, collection_id: str) -> None:
        albums, collection_name = self.client.get_collection_albums(collection_id)
        if collection_name:
            print(f"Collection: {collection_name}")

        for album in albums:
            print(f"Processing album: {album.title}")
            try:
                album.photos = self.client.get_album_photos(album.id)
            except Exception as error:
                print(f"Warning: Failed to get photos for album {album.id}: {error}")
                continue

            try:
                self.download_album(album)
            except Exception as error:
                print(f"Warning: Failed to download album {album.id}: {error}")

    def export_all_photos(self) -> None:
        albums = self.client.get_all_albums()
        print(f"Found {len(albums)} albums, processing with {DEFAULT_WORKERS} concurrent workers...")

        downloaded_files: set[str] = set()
        downloaded_files_lock = Lock()
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
            futures = [
                executor.submit(
                    self._process_album_with_tracking, worker_id, album, downloaded_files, downloaded_files_lock
                )
                for worker_id, album in enumerate(albums, start=1)
            ]
            for future in as_completed(futures):
                error = future.result()
                if error is not None:
                    errors.append(error)

        print("\nProcessing unorganized photos...")
        try:
            self.download_unorganized_photos(downloaded_files)
        except Exception as error:
            errors.append(str(error))

        if errors:
            print(f"Completed with {len(errors)} errors")
            for error in errors:
                print(f"  Error: {error}")
            raise RuntimeError(f"export completed with {len(errors)} errors")

        print("All photos processed successfully!")

    def export_all_photos_by_date(self) -> None:
        print("Getting all photos from your Flickr account...")
        all_photos = self.client.get_all_photos()
        if not all_photos:
            print("No photos found in your Flickr account")
            return

        print(f"Found {len(all_photos)} photos, processing with {DEFAULT_WORKERS} concurrent workers...")

        errors: list[str] = []
        success_count = 0

        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
            futures = [
                executor.submit(self._download_dated_photo, worker_id, photo)
                for worker_id, photo in enumerate(all_photos, start=1)
            ]
            for future in as_completed(futures):
                error = future.result()
                if error is None:
                    success_count += 1
                else:
                    errors.append(error)

        if errors:
            print(f"Downloaded {success_count} photos with {len(errors)} errors")
            for error in errors:
                print(f"  Error: {error}")
            raise RuntimeError(f"failed to download {len(errors)} photos by date")

        print(f"Successfully downloaded {success_count} photos by date")

    def _process_album_with_tracking(
        self,
        worker_id: int,
        album: Album,
        downloaded_files: set[str],
        downloaded_files_lock: Lock,
    ) -> str | None:
        worker_exporter = self.clone()
        print(f"[Worker {worker_id}] Processing album: {album.title}")

        try:
            album.photos = worker_exporter.client.get_album_photos(album.id)
        except Exception as error:
            return f"worker {worker_id}: failed to get photos for album {album.title}: {error}"

        with downloaded_files_lock:
            for photo in album.photos:
                downloaded_files.add(photo.filename)

        try:
            worker_exporter.download_album(album)
        except Exception as error:
            return f"worker {worker_id}: failed to download album {album.title}: {error}"

        print(f"[Worker {worker_id}] Completed album: {album.title} ({len(album.photos)} photos)")
        return None

    def download_album(self, album: Album) -> None:
        album_path = self.output_dir / album_directory_name(album)
        album_path.mkdir(parents=True, exist_ok=True)

        print(f"Downloading {len(album.photos)} photos to {album_path}")
        failed_downloads: list[str] = []

        for index, photo in enumerate(album.photos):
            if self.verbose:
                print(f"Downloading photo {index + 1}/{len(album.photos)}: {photo.title}")

            resolved_filename = photo_output_filename(photo)
            if not photo.filename.strip():
                print(f"  Warning: Photo {photo.id} has no filename from Flickr; using '{resolved_filename}'")

            photo_path = album_path / resolved_filename
            if photo_path.exists():
                if self.verbose:
                    print(f"  Skipping (already exists): {photo_path.name}")
                continue

            try:
                self.fetch_photo_metadata(photo)
            except Exception as error:
                print(f"  Warning: Failed to get metadata for {resolved_filename}: {error}")
                failed_downloads.append(resolved_filename)
                continue

            try:
                self.download_photo(photo, photo_path)
            except Exception as error:
                print(f"  Warning: Failed to download {resolved_filename}: {error}")
                failed_downloads.append(resolved_filename)
                continue

            try:
                self.metadata_writer.write_metadata(photo_path, photo)
            except Exception as error:
                print(f"  Error: Failed to write metadata for {resolved_filename}: {error}")
                try:
                    photo_path.unlink()
                except OSError as remove_error:
                    print(f"  Error: Also failed to remove incomplete photo {resolved_filename}: {remove_error}")
                failed_downloads.append(resolved_filename)
                continue

            if index < len(album.photos) - 1:
                self._sleep(0.1)

        if failed_downloads:
            raise RuntimeError(f"failed to download {len(failed_downloads)} photos: {failed_downloads}")

    def fetch_photo_metadata(self, photo: Photo) -> None:
        detailed_photo = self.client.get_photo_info(photo.id)
        photo.description = detailed_photo.description
        photo.tags = detailed_photo.tags
        photo.date_taken = detailed_photo.date_taken
        if detailed_photo.title:
            photo.title = detailed_photo.title

    def download_photo(self, photo: Photo, output_path: str | Path) -> None:
        output = Path(output_path)
        for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
            try:
                self._download_photo_attempt(photo.original_url, output)
                return
            except PermanentDownloadError:
                raise
            except RetryableDownloadError as error:
                if attempt == MAX_DOWNLOAD_ATTEMPTS:
                    raise RuntimeError(f"{error} after {MAX_DOWNLOAD_ATTEMPTS} attempts") from error

                delay = DOWNLOAD_RETRY_BASE_DELAY_SECONDS * attempt
                if self.verbose:
                    print(
                        f"  Download attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} failed for "
                        f"{output.name}: {error}. Retrying in {delay:.0f}s..."
                    )
                self._sleep(delay)

    def _download_photo_attempt(self, url: str, output_path: Path) -> None:
        # This is a per-photo ceiling for a single download attempt, not a limit on the overall export run.
        hard_timeout = max(self.client.request_timeout * 5, MIN_HARD_DOWNLOAD_TIMEOUT_PER_PHOTO_SECONDS)
        started_at = monotonic()

        try:
            with requests.get(
                url,
                stream=True,
                timeout=(self.client.request_timeout, self.client.request_timeout),
            ) as response:
                if response.status_code != 200:
                    error_message = f"HTTP {response.status_code}: {response.reason}"
                    if response.status_code == 429:
                        raise RetryableDownloadError(error_message)
                    raise PermanentDownloadError(error_message)
                with output_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 64):
                        if monotonic() - started_at > hard_timeout:
                            raise RetryableDownloadError(
                                f"download exceeded hard timeout of {hard_timeout:.0f}s for {output_path.name}"
                            )
                        if chunk:
                            handle.write(chunk)
        except requests.Timeout as error:
            raise RetryableDownloadError(
                f"download timed out after {self.client.request_timeout:.0f}s waiting for {output_path.name}"
            ) from error
        except requests.RequestException as error:
            raise RetryableDownloadError(f"download request failed for {output_path.name}: {error}") from error

    def download_unorganized_photos(self, downloaded_files: set[str]) -> None:
        print("Getting all photos from your Flickr account...")
        all_photos = self.client.get_all_photos()
        unorganized_photos = filter_unorganized_photos(all_photos, downloaded_files)

        if not unorganized_photos:
            print("No unorganized photos found - all photos are in photosets!")
            return

        print(
            f"Found {len(unorganized_photos)} unorganized photos to download, "
            f"processing with {DEFAULT_WORKERS} concurrent workers..."
        )

        unorganized_dir = self.output_dir / "Unorganized Photos"
        unorganized_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        success_count = 0

        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
            futures = [
                executor.submit(self._download_unorganized_photo, worker_id, photo, unorganized_dir)
                for worker_id, photo in enumerate(unorganized_photos, start=1)
            ]
            for future in as_completed(futures):
                error = future.result()
                if error is None:
                    success_count += 1
                else:
                    errors.append(error)

        if errors:
            print(f"Downloaded {success_count} unorganized photos with {len(errors)} errors")
            for error in errors:
                print(f"  Error: {error}")
            raise RuntimeError(f"failed to download {len(errors)} unorganized photos")

        print(f"Successfully downloaded {success_count} unorganized photos")

    def _download_unorganized_photo(self, worker_id: int, photo: Photo, unorganized_dir: Path) -> str | None:
        worker_exporter = self.clone()
        resolved_filename = photo_output_filename(photo)
        if worker_exporter.verbose:
            print(f"[Worker {worker_id}] Downloading unorganized photo: {photo.title or resolved_filename}")

        try:
            worker_exporter.fetch_photo_metadata(photo)
        except Exception as error:
            return f"worker {worker_id}: failed to process {resolved_filename}: {error}"

        return worker_exporter._download_photo_to_directory(worker_id, photo, unorganized_dir)

    def _download_dated_photo(self, worker_id: int, photo: Photo) -> str | None:
        worker_exporter = self.clone()
        resolved_filename = photo_output_filename(photo)
        if worker_exporter.verbose:
            print(f"[Worker {worker_id}] Downloading dated photo: {photo.title or resolved_filename}")
            print(f"[Worker {worker_id}] Fetching metadata for photo {photo.id}")

        try:
            worker_exporter.fetch_photo_metadata(photo)
        except Exception as error:
            return f"worker {worker_id}: failed to process {resolved_filename}: {error}"

        target_dir = worker_exporter.output_dir / photo_date_directory_name(photo)
        return worker_exporter._download_photo_to_directory(worker_id, photo, target_dir)

    def _download_photo_to_directory(self, worker_id: int, photo: Photo, target_dir: Path) -> str | None:
        resolved_filename = photo_output_filename(photo)
        if not photo.filename.strip():
            print(
                f"[Worker {worker_id}] Warning: Photo {photo.id} has no filename from Flickr; using '{resolved_filename}'"
            )

        photo_path = target_dir / resolved_filename
        if photo_path.exists():
            if self.verbose:
                print(f"[Worker {worker_id}] Skipping (already exists): {photo_path}")
            return None

        if self.verbose:
            print(f"[Worker {worker_id}] Saving photo {photo.id} to {photo_path}")

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            if self.verbose:
                print(f"[Worker {worker_id}] Downloading file for {photo.id}")
            self.download_photo(photo, photo_path)
            if self.verbose:
                print(f"[Worker {worker_id}] Writing metadata for {photo_path.name}")
            self.metadata_writer.write_metadata(photo_path, photo)
        except Exception as error:
            if photo_path.exists():
                try:
                    photo_path.unlink()
                except OSError as remove_error:
                    print(
                        f"[Worker {worker_id}] Error: Also failed to remove incomplete photo "
                        f"{resolved_filename}: {remove_error}"
                    )
            try:
                target_dir.rmdir()
            except OSError:
                pass
            return f"worker {worker_id}: failed to process {resolved_filename}: {error}"

        if self.verbose:
            print(f"[Worker {worker_id}] Completed {photo_path}")
        self._sleep(0.1)
        return None


def album_directory_name(album: Album) -> str:
    date_prefix = album.date_created.strftime("%Y-%m-%d") if album.date_created else "1970-01-01"
    return f"{date_prefix} {sanitize_filename(album.title)}"


def photo_date_directory_name(photo: Photo) -> str:
    if photo.date_taken is None:
        return "Unknown Date"
    return photo.date_taken.strftime("%Y-%m")


def photo_output_filename(photo: Photo) -> str:
    if photo.filename.strip():
        return sanitize_filename(photo.filename.strip())
    if photo.id.strip():
        return sanitize_filename(photo.id.strip())
    return "unknown-photo"


def sanitize_filename(filename: str) -> str:
    sanitized = filename
    for old, new in (
        ("/", "-"),
        ("\\", "-"),
        (":", "-"),
        ("*", "-"),
        ("?", "-"),
        ('"', "-"),
        ("<", "-"),
        (">", "-"),
        ("|", "-"),
    ):
        sanitized = sanitized.replace(old, new)
    return sanitized


def filter_unorganized_photos(all_photos: list[Photo], downloaded_files: set[str]) -> list[Photo]:
    return [photo for photo in all_photos if photo.filename not in downloaded_files]
