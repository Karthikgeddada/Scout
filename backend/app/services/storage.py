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
        data = resp.json()  # {"url": ..., "token": ...}
        # Supabase returns a RELATIVE signed-upload path ("/object/upload/sign/...");
        # clients PUT directly to it, so it must be an absolute URL.
        rel = (data.get("url") or "").lstrip("/")
        if rel:
            data["url"] = f"{self._settings.supabase_url}/storage/v1/{rel}"
        return data

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
        """Verify an uploaded object exists and get its size.

        Tries POST /object/info with both known request shapes, then falls
        back to a ranged GET — which definitively proves existence without
        downloading the whole object.
        """
        import structlog

        log = structlog.get_logger("storage.stat")
        bucket = self._settings.storage_bucket
        path = f"{bucket}/{storage_path}"
        with httpx.Client(timeout=30) as client:
            for payload in ([path], {"paths": [path]}):
                resp = client.post(self._url("/object/info"), headers=self._headers(), json=payload)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    info = item.get("data") if isinstance(item.get("data"), dict) else item
                    if item.get("error") or info is None:
                        continue
                    size = info.get("size")
                    if size is not None:
                        return {
                            "size": size,
                            "mime": info.get("mimetype") or info.get("contentType") or info.get("mime"),
                        }
            # Fallback: fetch only the first byte; 2xx proves the object exists.
            resp = client.get(
                self._url(f"/object/{path}"),
                headers={**self._headers(), "Range": "bytes=0-0"},
            )
            log.info("stat_fallback", path=path, status=resp.status_code)
            if resp.status_code < 400:
                crange = resp.headers.get("content-range")
                clen = resp.headers.get("content-length")
                if crange and "/" in crange:
                    size = int(crange.rsplit("/", 1)[-1])
                elif clen and clen.isdigit():
                    size = int(clen)
                else:
                    size = None
                return {"size": size, "mime": resp.headers.get("content-type")}
            log.warning("stat_failed", path=path, status=resp.status_code, body=resp.text[:300])
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
