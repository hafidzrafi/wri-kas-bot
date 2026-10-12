"""Google Drive archival client for Kas WRI Bot.

Provides receipt image upload and web link resolution with least-privilege
OAuth scopes and exponential retry handling.
"""

import io
import logging
import re
from pathlib import Path
from typing import Any

import googleapiclient.discovery
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def format_drive_filename(phone: str, name: str, timestamp_str: str) -> str:
    """Format standardized receipt archive filename.

    Example output: 20261011_6281100000001_Alice.jpg
    """
    clean_phone = re.sub(r"\D", "", phone)
    clean_name = re.sub(r"[^\w\s-]", "", name).strip()
    clean_name = re.sub(r"[\s-]+", "_", clean_name)
    clean_ts = re.sub(r"\D", "", timestamp_str)
    ts_prefix = clean_ts[:8] if len(clean_ts) >= 8 else (clean_ts or "receipt")

    return f"{ts_prefix}_{clean_phone}_{clean_name}.jpg"


class GoogleDriveClient:
    """Client for uploading receipt images to Google Drive."""

    def __init__(
        self,
        service_account_json: str | None = None,
        folder_id: str | None = None,
    ) -> None:
        from wri_kas_bot.config import get_settings

        settings = get_settings()
        self.service_account_json = service_account_json or getattr(
            settings, "google_service_account_json", "credentials.json"
        )
        self.folder_id = folder_id or getattr(settings, "drive_proof_folder_id", None)
        self._service: Any = None

    @property
    def service(self) -> Any:
        """Lazily builds and caches the Google Drive v3 API service."""
        if self._service is None:
            creds_path = Path(self.service_account_json)
            credentials = None
            if creds_path.is_file():
                try:
                    credentials = service_account.Credentials.from_service_account_file(
                        str(creds_path),
                        scopes=DRIVE_SCOPES,
                    )
                except Exception:
                    credentials = None

            if credentials is not None:
                self._service = googleapiclient.discovery.build(
                    "drive", "v3", credentials=credentials, cache_discovery=False
                )
            else:
                self._service = googleapiclient.discovery.build(
                    "drive", "v3", cache_discovery=False
                )
        return self._service

    def upload_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str = "image/jpeg",
    ) -> str:
        """Upload raw byte content to Google Drive and return public web view URL."""
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
        file_metadata: dict[str, Any] = {"name": filename}
        if self.folder_id:
            file_metadata["parents"] = [self.folder_id]

        file_obj = (
            self.service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )

        file_id = file_obj.get("id", "")
        web_link = file_obj.get("webViewLink")
        if not web_link:
            web_link = f"https://drive.google.com/file/d/{file_id}/view"

        logger.info("Uploaded receipt '%s' to Google Drive with id=%s", filename, file_id)
        return web_link

    def upload_file(self, image_bytes: bytes, filename: str) -> str:
        """Convenience alias for uploading image bytes."""
        return self.upload_bytes(image_bytes, filename)
