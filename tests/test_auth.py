from __future__ import annotations

import stat

from flickr_exporter.auth import load_credentials, merge_credentials, save_credentials
from flickr_exporter.models import Credentials


def test_credentials_round_trip_and_permissions(tmp_path):
    creds_path = tmp_path / "creds.yml"
    original = Credentials(
        api_key="key",
        api_secret="secret",
        oauth_token="token",
        oauth_token_secret="token-secret",
    )

    save_credentials(creds_path, original)
    loaded = load_credentials(creds_path)

    assert loaded == original
    assert stat.S_IMODE(creds_path.stat().st_mode) == 0o600


def test_merge_credentials_prefers_explicit_values():
    preferred = Credentials(api_key="flag-key", oauth_token_secret="flag-secret")
    fallback = Credentials(
        api_key="file-key",
        api_secret="file-secret",
        oauth_token="file-token",
        oauth_token_secret="file-token-secret",
    )

    merged = merge_credentials(preferred, fallback)

    assert merged.api_key == "flag-key"
    assert merged.api_secret == "file-secret"
    assert merged.oauth_token == "file-token"
    assert merged.oauth_token_secret == "flag-secret"
