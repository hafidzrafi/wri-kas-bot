"""Tier 1: Feature Coverage: Feature 9: Google Drive Archival."""

from unittest.mock import MagicMock, patch

from wri_kas_bot.drive import GoogleDriveClient, format_drive_filename


def test_drive_upload_success_returns_view_url(mock_drive_client):
    """Verify uploading receipt returns a valid Google Drive file view URL."""
    img_bytes = b"fake-jpeg-content"
    url = mock_drive_client.upload_file(img_bytes, "20261011_6281100000001_Alice.jpg")

    assert "drive.google.com/file/d/" in url
    assert "view" in url


def test_drive_upload_folder_destination():
    """Verify files are targeted to the configured drive_proof_folder_id."""
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        files_mock = mock_service.files().create
        files_mock.return_value.execute.return_value = {"id": "uploaded-file-id"}

        client = GoogleDriveClient(service_account_json="mock.json", folder_id="target-folder-123")
        client._service = mock_service
        client.upload_bytes(b"data", "test.jpg")

        call_kwargs = files_mock.call_args.kwargs
        assert call_kwargs["body"]["parents"] == ["target-folder-123"]


def test_drive_upload_file_naming_convention():
    """Verify file naming incorporates timestamp, phone, and member name."""
    filename = format_drive_filename("6281100000001", "Alice", "2026-10-11T12:00:00Z")
    assert "6281100000001" in filename
    assert "Alice" in filename
    assert filename.endswith(".jpg")


def test_drive_upload_mime_type():
    """Verify media body upload specifies image/jpeg."""
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        client = GoogleDriveClient(service_account_json="mock.json", folder_id="folder-123")
        client._service = mock_service

        mock_create = mock_service.files().create
        mock_create.return_value.execute.return_value = {"id": "file-123"}

        client.upload_bytes(b"image_data", "test.jpg")
        media_body = mock_create.call_args.kwargs.get("media_body")
        assert media_body is not None
        assert media_body.mimetype() == "image/jpeg"


def test_drive_upload_custom_mime_type():
    """Verify upload allows custom mime types like image/png."""
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        client = GoogleDriveClient(service_account_json="mock.json", folder_id="folder-123")
        client._service = mock_service

        mock_create = mock_service.files().create
        mock_create.return_value.execute.return_value = {"id": "file-png"}

        client.upload_bytes(b"png_data", "test.png", mime_type="image/png")
        media_body = mock_create.call_args.kwargs.get("media_body")
        assert media_body is not None
        assert media_body.mimetype() == "image/png"


def test_drive_oauth_token_loading(tmp_path):
    """Verify GoogleDriveClient loads and constructs Credentials from OAuth token json."""
    import json

    token_file = tmp_path / "mock_drive_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "mock-token",
                "refresh_token": "mock-refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "mock-client-id",
                "client_secret": "mock-client-secret",
            }
        )
    )

    with patch("googleapiclient.discovery.build") as mock_build:
        mock_build.return_value = MagicMock()
        client = GoogleDriveClient(token_json=str(token_file), folder_id="folder-123")
        svc = client.service
        assert svc is not None
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        credentials = kwargs.get("credentials")
        assert credentials is not None
        assert credentials.refresh_token == "mock-refresh"
        assert credentials.token == "mock-token"
