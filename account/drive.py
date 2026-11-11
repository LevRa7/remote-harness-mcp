"""Per-user Google Drive connector.

Tokens are stored per account, encrypted by ``account.crypto``. The connector
uses the narrow ``drive.file`` scope: only files created/opened by this app are
visible, not the user's whole Drive.
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Optional

from . import auth, crypto

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
MAX_UPLOAD = 100 * 1024 * 1024


class DriveError(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _expires_at(seconds: Any) -> str:
    try:
        value = int(seconds)
    except Exception:
        value = 3600
    return (_now() + datetime.timedelta(seconds=max(60, value))).isoformat(timespec="seconds")


def save_tokens(config, store, account_id: str, tokens: Dict[str, Any]) -> None:
    payload = {
        "access_token": str(tokens.get("access_token") or ""),
        "refresh_token": str(tokens.get("refresh_token") or ""),
        "expires_at": _expires_at(tokens.get("expires_in")),
        "scope": str(tokens.get("scope") or auth.DRIVE_SCOPES),
    }
    if not payload["access_token"]:
        raise DriveError("Google did not return an access token.")
    store.save_google_tokens(
        account_id,
        crypto.encrypt(payload, config.session_secret),
        payload["scope"],
    )


def access_token(config, store, account_id: str) -> str:
    record = store.google_tokens(account_id)
    if not record:
        raise DriveError("Google Drive is not connected for this account.")
    tokens = crypto.decrypt(record["payload"], config.session_secret)
    expires = str(tokens.get("expires_at") or "")
    try:
        expired = datetime.datetime.fromisoformat(expires) <= _now() + datetime.timedelta(seconds=90)
    except Exception:
        expired = True
    if not expired and tokens.get("access_token"):
        return str(tokens["access_token"])
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        raise DriveError("Drive session expired; reconnect Google Drive.")
    try:
        fresh = auth.refresh_access_token(config, refresh)
    except Exception as exc:
        raise DriveError("Drive token refresh failed: %s" % exc)
    tokens["access_token"] = str(fresh.get("access_token") or "")
    tokens["expires_at"] = _expires_at(fresh.get("expires_in"))
    if fresh.get("refresh_token"):
        tokens["refresh_token"] = str(fresh["refresh_token"])
    if not tokens["access_token"]:
        raise DriveError("Drive token refresh returned no access token.")
    store.save_google_tokens(account_id, crypto.encrypt(tokens, config.session_secret), str(tokens.get("scope") or ""))
    return tokens["access_token"]


def _call(token: str, url: str, method: str = "GET", data: Optional[bytes] = None,
          headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise DriveError("Drive API HTTP %s: %s" % (exc.code, detail))
    except Exception as exc:
        raise DriveError("Drive API failed: %s" % exc)


def list_files(token: str, page_size: int = 50) -> list:
    params = urllib.parse.urlencode({
        "pageSize": max(1, min(100, int(page_size))),
        "fields": "files(id,name,mimeType,size,modifiedTime)",
        "q": "trashed = false",
        "orderBy": "modifiedTime desc",
    })
    payload = _call(token, API + "/files?" + params)
    files = payload.get("files", [])
    return files if isinstance(files, list) else []


def download_file(token: str, file_id: str, destination: str) -> Dict[str, Any]:
    destination = os.path.realpath(os.path.expanduser(destination))
    # The caller (cabinet/MCP) must confine this to the workspace; the connector
    # itself only refuses obviously dangerous targets.
    if destination in ("/", os.path.expanduser("~")):
        raise DriveError("refusing to overwrite a root directory")
    request = urllib.request.Request(
        API + "/files/" + urllib.parse.quote(str(file_id)) + "?alt=media"
    )
    request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as handle:
            written = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD:
                    raise DriveError("file is larger than the 100 MB limit")
                handle.write(chunk)
    except DriveError:
        raise
    except Exception as exc:
        raise DriveError("Drive download failed: %s" % exc)
    return {"path": destination, "bytes": written}


def upload_file(token: str, source: str, name: str = "", folder_id: str = "") -> Dict[str, Any]:
    source = os.path.realpath(os.path.expanduser(source))
    if not os.path.isfile(source):
        raise DriveError("not a regular file: %s" % source)
    size = os.path.getsize(source)
    if size > MAX_UPLOAD:
        raise DriveError("file is larger than the 100 MB limit")
    metadata: Dict[str, Any] = {"name": name or os.path.basename(source)}
    if folder_id:
        metadata["parents"] = [folder_id]
    boundary = "rhm-" + uuid.uuid4().hex
    content_type = mimetypes.guess_type(source)[0] or "application/octet-stream"
    with open(source, "rb") as handle:
        body = handle.read()
    parts = [
        ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n" % (
            boundary, json.dumps(metadata))).encode(),
        ("--%s\r\nContent-Type: %s\r\n\r\n" % (boundary, content_type)).encode(),
        body,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ]
    request = urllib.request.Request(
        UPLOAD + "/files?uploadType=multipart&fields=id,name,mimeType,size",
        data=b"".join(parts), method="POST",
    )
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "multipart/related; boundary=%s" % boundary)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DriveError("Drive upload HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")[:500]))
    except Exception as exc:
        raise DriveError("Drive upload failed: %s" % exc)
