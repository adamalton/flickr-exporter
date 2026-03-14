from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from flickr_exporter.cli import DEFAULT_CREDS_FILE, app
from flickr_exporter.models import Credentials

runner = CliRunner()


def test_auth_accepts_api_flags_after_subcommand(monkeypatch, tmp_path):
    saved: dict[str, object] = {}

    def fake_perform_oauth_flow(api_key: str, api_secret: str, prompt=input, echo=print):
        saved["api_key"] = api_key
        saved["api_secret"] = api_secret
        return Credentials(
            api_key=api_key,
            api_secret=api_secret,
            oauth_token="token",
            oauth_token_secret="secret",
        )

    def fake_save_credentials(path: str | Path, credentials: Credentials) -> None:
        saved["path"] = Path(path)
        saved["credentials"] = credentials

    monkeypatch.setattr("flickr_exporter.cli.perform_oauth_flow", fake_perform_oauth_flow)
    monkeypatch.setattr("flickr_exporter.cli.save_credentials", fake_save_credentials)

    result = runner.invoke(
        app,
        [
            "auth",
            "-k",
            "key-123",
            "-s",
            "secret-456",
            "--save-creds",
            str(tmp_path / "creds.yml"),
        ],
    )

    assert result.exit_code == 0
    assert saved["api_key"] == "key-123"
    assert saved["api_secret"] == "secret-456"
    assert saved["path"] == tmp_path / "creds.yml"


def test_root_loads_default_creds_file_when_present(monkeypatch, tmp_path):
    creds_path = tmp_path / DEFAULT_CREDS_FILE
    creds_path.write_text(
        "\n".join(
            [
                "api_key: file-key",
                "api_secret: file-secret",
                "oauth_token: file-token",
                "oauth_token_secret: file-token-secret",
            ]
        ),
        encoding="utf-8",
    )

    captured: dict[str, Credentials] = {}

    def fake_build_exporter(config):
        captured["credentials"] = config.credentials
        raise RuntimeError("stop after config")

    monkeypatch.setattr("flickr_exporter.cli._build_exporter", fake_build_exporter)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["all"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "credentials" in captured
    assert captured["credentials"].api_key == "file-key"
    assert captured["credentials"].oauth_token == "file-token"


def test_missing_default_creds_file_does_not_error():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["all"])

        assert result.exit_code == 1
        assert "Provide them via flags or credentials file (-c)" in result.output


def test_date_command_calls_date_export(monkeypatch):
    called: dict[str, bool] = {"exported": False}

    class FakeExporter:
        def export_all_photos_by_date(self) -> None:
            called["exported"] = True

    monkeypatch.setattr(
        "flickr_exporter.cli._build_exporter",
        lambda config: FakeExporter(),
    )

    result = runner.invoke(
        app,
        [
            "--api-key",
            "key",
            "--api-secret",
            "secret",
            "--oauth-token",
            "token",
            "--oauth-token-secret",
            "token-secret",
            "date",
        ],
    )

    assert result.exit_code == 0
    assert called["exported"] is True
    assert "Successfully exported all photos by date" in result.output
