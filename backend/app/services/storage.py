"""Private Supabase Storage access (REST, service-role; backend only).

Files live in a private bucket; clients only ever receive short-lived signed URLs."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.errors import StorageError


class SupabaseStorage:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _headers(self) -> dict[str, str]:
        key = self._settings.supabase_service_role_key
        if not key:
            raise StorageError("service role key not configured")
        return {"Authorization": f"Bearer {key}", "apikey": key}

    def _url(self, path: str) -> str:
        return f"{self._settings.supabase_url}/storage/v1{path}"

    def create_signed_upload_url(self, storage_path: str, expires_seconds: int = 900) -> dict:
        bucket = self._settings.storage_bucket
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                self._url(f"/object/upload/sign/{bucket}/{storage_path}"),
                headers=self._headers(),
                json={"expiresIn": expires_seconds},
            )
        if resp.status_code >= 400:
            raise StorageError(f"upload URL failed: {resp.text}")
        return resp.json()  # {"url": ..., "token": ...}

    def create_signed_download_url(self, storage_path: str, expires_seconds: int = 600) -> str:
        bucket = self._settings.storage_bucket
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                self._url("/object/sign"),
                headers=self._headers(),
                json={"expiresIn": expires_seconds, "paths": [f"{bucket}/{storage_path}"]},
            )
        if resp.status_code >= 400:
            raise StorageError(f"sign failed: {resp.text}")
        data = resp.json()
        if isinstance(data, list) and data:
            rel = data[0].lstrip("/")
            return f"{self._settings.supabase_url}/storage/v1{rel}"
        return f"{self._settings.supabase_url}/storage/v1{data.get('signedURL', '')}"

    def download_bytes(self, storage_path: str) -> bytes:
        bucket = self._settings.storage_bucket
        with httpx.Client(timeout=120) as client:
            resp = client.get(self._url(f"/object/{bucket}/{storage_path}"), headers=self._headers())
        if resp.status_code >= 400:
            raise StorageError(f"download failed ({resp.status_code})")
        return resp.content

    def stat(self, storage_path: str) -> dict | None:
        """Verify an uploaded object exists and get its size."""
        bucket = self._settings.storage_bucket
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                self._url("/object/info"),
                headers=self._headers(),
                json=[f"{bucket}/{storage_path}"],
            )
        if resp.status_code >= 400:
            return None
        data = resp.json()
        if isinstance(data, list) and data:
            first = data[0]
            return {"size": first.get("size"), "mime": first.get("mimetype")}
        return None

    def delete(self, storage_path: str) -> None:
        bucket = self._settings.storage_bucket
        with httpx.Client(timeout=30) as client:
            client.request(
                "DELETE",
                self._url("/object"),
                headers=self._headers(),
                json={"prefixes": [f"{bucket}/{storage_path}"]},
            )
