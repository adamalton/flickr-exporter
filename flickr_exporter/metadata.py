from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from flickr_exporter.models import Photo


class MetadataWriter:
    def __init__(self, exiftool_path: str | None = None) -> None:
        self.exiftool_path = exiftool_path or shutil.which("exiftool")
        if not self.exiftool_path:
            raise RuntimeError("could not initialize exiftool: exiftool was not found in PATH")

    def write_metadata(self, photo_path: str | Path, photo: Photo) -> None:
        command = [self.exiftool_path, "-overwrite_original"]

        if photo.title:
            command.append(f"-IPTC:ObjectName={photo.title}")
        if photo.description:
            command.append(f"-IPTC:Caption-Abstract={photo.description}")
        if photo.tags:
            command.append("-IPTC:Keywords=")
            command.extend(f"-IPTC:Keywords+={tag}" for tag in photo.tags)
            command.append("-XMP:Subject=")
            command.extend(f"-XMP:Subject+={tag}" for tag in photo.tags)

        command.append(str(photo_path))

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip() or "unknown exiftool error"
            raise RuntimeError(output)
