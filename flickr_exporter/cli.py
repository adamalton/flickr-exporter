from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from flickr_exporter.auth import load_credentials, merge_credentials, perform_oauth_flow, save_credentials
from flickr_exporter.exporter import FlickrExporter
from flickr_exporter.flickr_api import FlickrClient
from flickr_exporter.metadata import MetadataWriter
from flickr_exporter.models import Credentials

app = typer.Typer(add_completion=False, help="Export original-resolution photos from Flickr")
DEFAULT_CREDS_FILE = Path("creds.yml")


@dataclass(slots=True)
class AppConfig:
    credentials: Credentials
    output_dir: str
    verbose: bool


def _load_config(
    api_key: str,
    api_secret: str,
    output_dir: str,
    oauth_token: str,
    oauth_token_secret: str,
    creds_file: Path | None,
    verbose: bool,
) -> AppConfig:
    credentials = Credentials(
        api_key=api_key,
        api_secret=api_secret,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
    )
    if creds_file is not None and creds_file.exists():
        credentials = merge_credentials(credentials, load_credentials(creds_file))
    return AppConfig(credentials=credentials, output_dir=output_dir, verbose=verbose)


def _merge_command_credentials(base: Credentials, overrides: Credentials) -> Credentials:
    return merge_credentials(overrides, base)


def _load_optional_credentials(credentials: Credentials, creds_file: Path | None) -> Credentials:
    if creds_file is None:
        return credentials
    if not creds_file.exists():
        if creds_file == DEFAULT_CREDS_FILE:
            return credentials
        raise typer.BadParameter(f"Credentials file not found: {creds_file}")
    return merge_credentials(credentials, load_credentials(creds_file))


def _require_api_credentials(credentials: Credentials, *, auth_only: bool = False) -> None:
    if credentials.api_key and credentials.api_secret:
        return

    if auth_only:
        typer.echo("Error: Both API key and API secret are required for authentication")
    else:
        typer.echo("Error: Both API key and API secret are required")
    typer.echo("Provide them via flags or credentials file (-c)")
    raise typer.Exit(code=1)


def _build_exporter(config: AppConfig) -> FlickrExporter:
    if not config.credentials.oauth_token or not config.credentials.oauth_token_secret:
        raise typer.BadParameter("OAuth tokens are required. Please run 'flickr-exporter auth' first to authenticate")

    client = FlickrClient(credentials=config.credentials, verbose=config.verbose)
    return FlickrExporter(
        client=client,
        output_dir=config.output_dir,
        metadata_writer=MetadataWriter(),
        verbose=config.verbose,
    )


@app.callback()
def root(
    ctx: typer.Context,
    api_key: Annotated[str, typer.Option("--api-key", "-k", help="Flickr API Key")] = "",
    api_secret: Annotated[str, typer.Option("--api-secret", "-s", help="Flickr API Secret")] = "",
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output directory for exported photos")
    ] = "./flickr-export",
    oauth_token: Annotated[str, typer.Option("--oauth-token", help="OAuth token")] = "",
    oauth_token_secret: Annotated[str, typer.Option("--oauth-token-secret", help="OAuth token secret")] = "",
    creds_file: Annotated[
        Path,
        typer.Option("--creds-file", "--creds", "-c", help="Credentials file (YAML)"),
    ] = DEFAULT_CREDS_FILE,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose logging")] = False,
) -> None:
    ctx.obj = _load_config(
        api_key=api_key,
        api_secret=api_secret,
        output_dir=output,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        creds_file=creds_file,
        verbose=verbose,
    )


@app.command()
def auth(
    ctx: typer.Context,
    api_key: Annotated[str, typer.Option("--api-key", "-k", help="Flickr API Key")] = "",
    api_secret: Annotated[str, typer.Option("--api-secret", "-s", help="Flickr API Secret")] = "",
    creds_file: Annotated[
        Path,
        typer.Option("--creds-file", "--creds", "-c", help="Credentials file (YAML)"),
    ] = DEFAULT_CREDS_FILE,
    save_creds: Annotated[Path | None, typer.Option("--save-creds", help="Save credentials to this YAML file")] = None,
) -> None:
    config: AppConfig = ctx.obj
    credentials = _merge_command_credentials(
        config.credentials,
        Credentials(
            api_key=api_key,
            api_secret=api_secret,
        ),
    )
    credentials = _load_optional_credentials(credentials, creds_file)

    _require_api_credentials(credentials, auth_only=True)

    try:
        credentials = perform_oauth_flow(
            credentials.api_key,
            credentials.api_secret,
            echo=typer.echo,
        )
    except Exception as error:
        typer.echo(f"Error during authentication: {error}")
        raise typer.Exit(code=1) from error

    if save_creds is not None:
        save_credentials(save_creds, credentials)
        typer.echo(f"Credentials saved to {save_creds}")
        typer.echo(f"You can now use: flickr-exporter -c {save_creds} [command]")
    else:
        typer.echo("")
        typer.echo("Save these tokens and use them with:")
        typer.echo(f"--oauth-token {credentials.oauth_token} --oauth-token-secret {credentials.oauth_token_secret}")


@app.command()
def album(ctx: typer.Context, album_ids: list[str] = typer.Argument(..., help="One or more album IDs")) -> None:
    config: AppConfig = ctx.obj
    _require_api_credentials(config.credentials)

    try:
        exporter = _build_exporter(config)
    except Exception as error:
        typer.echo(f"Error creating exporter: {error}")
        raise typer.Exit(code=1) from error

    has_errors = False
    for album_id in album_ids:
        try:
            exporter.export_album(album_id)
            typer.echo(f"Successfully exported album {album_id}")
        except Exception as error:
            typer.echo(f"Error exporting album {album_id}: {error}")
            has_errors = True

    if has_errors:
        raise typer.Exit(code=1)


@app.command()
def collection(
    ctx: typer.Context,
    collection_ids: list[str] = typer.Argument(..., help="One or more collection IDs"),
) -> None:
    config: AppConfig = ctx.obj
    _require_api_credentials(config.credentials)

    try:
        exporter = _build_exporter(config)
    except Exception as error:
        typer.echo(f"Error creating exporter: {error}")
        raise typer.Exit(code=1) from error

    has_errors = False
    for collection_id in collection_ids:
        try:
            typer.echo(f"Exporting collection {collection_id}...")
            exporter.export_collection(collection_id)
            typer.echo(f"Successfully exported collection {collection_id}")
        except Exception as error:
            typer.echo(f"Error exporting collection {collection_id}: {error}")
            has_errors = True

    if has_errors:
        raise typer.Exit(code=1)


@app.command("all")
def export_all(ctx: typer.Context) -> None:
    config: AppConfig = ctx.obj
    _require_api_credentials(config.credentials)

    try:
        exporter = _build_exporter(config)
    except Exception as error:
        typer.echo(f"Error creating exporter: {error}")
        raise typer.Exit(code=1) from error

    typer.echo("Exporting all photos...")
    try:
        exporter.export_all_photos()
    except Exception as error:
        typer.echo(f"Error exporting all photos: {error}")
        raise typer.Exit(code=1) from error

    typer.echo("Successfully exported all photos")


@app.command()
def date(ctx: typer.Context) -> None:
    config: AppConfig = ctx.obj
    _require_api_credentials(config.credentials)

    try:
        exporter = _build_exporter(config)
    except Exception as error:
        typer.echo(f"Error creating exporter: {error}")
        raise typer.Exit(code=1) from error

    typer.echo("Exporting all photos by date...")
    try:
        exporter.export_all_photos_by_date()
    except Exception as error:
        typer.echo(f"Error exporting all photos by date: {error}")
        raise typer.Exit(code=1) from error

    typer.echo("Successfully exported all photos by date")


if __name__ == "__main__":
    app()
