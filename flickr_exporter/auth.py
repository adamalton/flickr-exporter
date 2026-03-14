from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

import yaml
from requests_oauthlib import OAuth1Session

from flickr_exporter.models import Credentials

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"


def save_credentials(path: str | Path, credentials: Credentials) -> None:
    target = Path(path)
    payload = yaml.safe_dump(asdict(credentials), sort_keys=False)
    target.write_text(payload, encoding="utf-8")
    target.chmod(0o600)


def load_credentials(path: str | Path) -> Credentials:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Credentials(
        api_key=str(raw.get("api_key", "") or ""),
        api_secret=str(raw.get("api_secret", "") or ""),
        oauth_token=str(raw.get("oauth_token", "") or ""),
        oauth_token_secret=str(raw.get("oauth_token_secret", "") or ""),
    )


def merge_credentials(preferred: Credentials, fallback: Credentials) -> Credentials:
    return Credentials(
        api_key=preferred.api_key or fallback.api_key,
        api_secret=preferred.api_secret or fallback.api_secret,
        oauth_token=preferred.oauth_token or fallback.oauth_token,
        oauth_token_secret=preferred.oauth_token_secret or fallback.oauth_token_secret,
    )


def perform_oauth_flow(
    api_key: str,
    api_secret: str,
    prompt: Callable[[str], str] = input,
    echo: Callable[[str], None] = print,
) -> Credentials:
    session = OAuth1Session(
        api_key,
        client_secret=api_secret,
        callback_uri="oob",
    )

    echo("Getting request token...")
    echo(f"Using API Key: {api_key}")
    masked_secret = f"{api_secret[:8]}..." if len(api_secret) >= 8 else api_secret
    echo(f"Using API Secret: {masked_secret}")

    request_token = session.fetch_request_token(REQUEST_TOKEN_URL)
    authorization_url = session.authorization_url(AUTHORIZE_URL, perms="read")

    echo("")
    echo("Please visit this URL to authorize the application:")
    echo(authorization_url)
    echo("")
    verifier = prompt("After authorizing, enter the verification code: ").strip()

    echo("Getting access token...")
    authorized = OAuth1Session(
        api_key,
        client_secret=api_secret,
        resource_owner_key=request_token["oauth_token"],
        resource_owner_secret=request_token["oauth_token_secret"],
        verifier=verifier,
    )
    access_token = authorized.fetch_access_token(ACCESS_TOKEN_URL)

    echo("")
    echo("Authentication successful!")
    echo(f"OAuth Token: {access_token['oauth_token']}")
    echo(f"OAuth Token Secret: {access_token['oauth_token_secret']}")

    return Credentials(
        api_key=api_key,
        api_secret=api_secret,
        oauth_token=access_token["oauth_token"],
        oauth_token_secret=access_token["oauth_token_secret"],
    )
