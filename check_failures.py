#!/usr/bin/env python

import argparse
import json
import os
import sys


def get_export_path() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        default=".",
        help="Directory containing failures.json and album folders",
    )
    args = parser.parse_args()
    path = args.path
    if not path:
        print("🚨 No path provided. Please provide a path to the export directory with the --path argument.")
        sys.exit(1)
    if not os.path.exists(path):
        print(
            f"🚨 Path {path} does not exist. Please provide a valid path to the export directory with the --path argument."
        )
        sys.exit(1)
    return path


def get_album_dirs(path: str) -> list[str]:
    album_dirs = os.listdir(path)
    return [dir for dir in album_dirs if os.path.isdir(os.path.join(path, dir))]


def get_failures() -> list[dict]:
    with open(os.path.join(get_export_path(), "failures.json")) as f:
        return json.load(f)["failures"]


def main():
    album_dirs = get_album_dirs(get_export_path())
    failures = get_failures()
    found_photos = []
    not_found_photos = []
    for failure in failures:
        filename = failure["path"].split("/")[-1]
        for album_dir in album_dirs:
            album_contents = os.listdir(os.path.join(get_export_path(), album_dir))
            if filename in album_contents:
                print(f"Found {filename} in {album_dir}")
                found_photos.append({"failure": failure, "album_dir": album_dir})
                break
        else:
            print(f"Did not find {filename} in any album")
            not_found_photos.append(failure)

    print(f"Found {len(found_photos)} photos. Did not find {len(not_found_photos)} photos.")


class FoundPhoto(Exception):
    pass


if __name__ == "__main__":
    main()
