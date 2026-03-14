# flickr-exporter

A Python command-line tool to download and archive your Flickr photos with metadata preservation.

## Features

- Download all photos from your Flickr account
- Download specific albums (photosets) or collections
- Preserve photo metadata (title, description, tags) as IPTC/XMP data via `exiftool`
- Organize downloads by album with date prefixes
- Resume interrupted runs by skipping existing files
- Download unorganized photos into a separate folder
- Authenticate with Flickr using OAuth and reusable YAML credentials
- Use concurrent workers for large exports
- Retry rate-limited requests for photo metadata
- Download original-resolution photos when Flickr exposes an original URL

## Requirements

- Python 3.11 or later
- ExifTool
  - macOS: `brew install exiftool`
  - Linux: `sudo apt-get install libimage-exiftool-perl`
  - Windows: Download from [exiftool.org](https://exiftool.org)

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

If you prefer not to use a virtual environment, install the package into whatever Python environment you normally use.

## Usage

### Authentication

Before using `flickr-exporter`, create a Flickr API key and secret at <https://www.flickr.com/services/apps/create/>.

Authenticate once and save your credentials:

```bash
flickr-exporter auth -k YOUR_API_KEY -s YOUR_API_SECRET --save-creds creds.yml
```

This prints an authorization URL, prompts for the verification code, and writes a reusable YAML credentials file containing:

- `api_key`
- `api_secret`
- `oauth_token`
- `oauth_token_secret`

### Export Commands

Download all photos, organized by album:

```bash
flickr-exporter all -o /path/to/output/directory
```

Download one or more albums:

```bash
flickr-exporter album ALBUM_ID ALBUM_ID_2 -o /path/to/output/directory
```

Download one or more collections:

```bash
flickr-exporter collection COLLECTION_ID COLLECTION_ID_2 -o /path/to/output/directory
```

Photos not in any album are written to `Unorganized Photos/`.

### Common Options

- `-c, --creds, --creds-file`: Path to credentials file, default `creds.yml`
- `-k, --api-key`: Flickr API key
- `-s, --api-secret`: Flickr API secret
- `--oauth-token`: OAuth token override
- `--oauth-token-secret`: OAuth token secret override
- `-o, --output`: Output directory, default `./flickr-export`
- `-v, --verbose`: Print per-photo progress information

### Output Structure

```text
output-directory/
├── 2023-01-15 Vacation Photos/
│   ├── IMG_001.jpg
│   ├── IMG_002.jpg
│   └── ...
├── 2023-02-20 Birthday Party/
│   └── ...
└── Unorganized Photos/
    └── ...
```

Albums are prefixed with their creation date in `YYYY-MM-DD` format for chronological sorting.

### Metadata Preservation

The exporter writes the following metadata to each downloaded photo:

- `IPTC:ObjectName`: Photo title
- `IPTC:Caption-Abstract`: Photo description
- `IPTC:Keywords`: Photo tags
- `XMP:Subject`: Photo tags

If metadata writing fails, the downloaded file is removed so reruns can cleanly retry it.

### Examples

```bash
# One-time auth
flickr-exporter auth -k abc123 -s xyz789 --save-creds creds.yml

# Download all photos
flickr-exporter all -o ~/Pictures/Flickr-Backup

# Download a specific album with verbose output
flickr-exporter album 72157694563874100 -o ~/Pictures -v

# Download photos from a collection
flickr-exporter collection 12345-67890 -o ~/Pictures/Collections
```

## Development

```bash
make dev-install
make test
make build
```

## Author

Claude wrote this code with management by Chris Dzombak ([dzombak.com](https://www.dzombak.com) / [github.com/cdzombak](https://www.github.com/cdzombak)).

## License

GNU GPL v3; see [LICENSE](LICENSE) in this repo.
