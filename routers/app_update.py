"""
Khojati Inventory Management System - App Update & OTA Router.

Provides endpoints to query the latest mobile app version, download the
compiled APK, and allow admins to publish new version metadata.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from auth import get_current_admin

logger = logging.getLogger("khojati.app_update")

router = APIRouter(prefix="/api/app", tags=["App Updates"])

VERSION_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "version.json"
)

DEFAULT_VERSION_DATA = {
    "latest_version": "1.0.0",
    "version_code": 1,
    "min_supported_version": "1.0.0",
    "apk_url": "/api/app/download",
    "release_notes": "Initial cloud release with live stock, dual portals, and barcode scanning.",
    "force_update": False,
    "published_at": "2026-11-08T11:00:00Z",
}


def _load_version_data() -> dict:
    if os.path.exists(VERSION_FILE_PATH):
        try:
            with open(VERSION_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read version.json: %s", e)
    return {
        "latest_version": "1.0.0",
        "version_code": 1,
        "min_supported_version": "1.0.0",
        "apk_url": "/api/app/download",
        "release_notes": "Initial cloud release with live stock, dual portals, and barcode scanning.",
        "force_update": False,
        "published_at": "2026-11-08T11:00:00Z",
    }


def _save_version_data(data: dict) -> None:
    try:
        with open(VERSION_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Failed to write version.json: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist version metadata: {str(e)}",
        )


class UpdateVersionRequest(BaseModel):
    latest_version: str = Field(..., example="1.0.1")
    version_code: int = Field(..., example=2)
    min_supported_version: Optional[str] = Field(None, example="1.0.0")
    apk_url: Optional[str] = Field(None, example="/api/app/download")
    release_notes: Optional[str] = Field(None, example="Fixed barcode beep sound and improved stock loading speed.")
    force_update: Optional[bool] = Field(False, example=False)


@router.get("/version")
def get_latest_app_version():
    """
    Returns the latest version metadata for the Khojati mobile app.
    The Flutter app queries this on startup to prompt updates.
    """
    return _load_version_data()


@router.get("/download")
def download_app_apk():
    """
    Directly streams the latest Khojati_Inventory.apk file or redirects
    to the configured download URL.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, "static", "apk", "Khojati_Inventory.apk"),
        os.path.join(base_dir, "uploads", "apk", "Khojati_Inventory.apk"),
        os.path.join(base_dir, "Khojati_Inventory.apk"),
        os.path.join(os.path.dirname(base_dir), "Khojati_Inventory.apk"),
    ]

    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return FileResponse(
                path=candidate,
                filename="Khojati_Inventory.apk",
                media_type="application/vnd.android.package-archive",
            )

    version_data = _load_version_data()
    apk_url = version_data.get("apk_url")
    if apk_url and apk_url.startswith("http") and not apk_url.endswith("/api/app/download"):
        return RedirectResponse(url=apk_url, status_code=302)

    # Fallback to GitHub releases link if available
    github_release_url = "https://github.com/chauhanhasnain78-byte/Khojati-backend/releases/latest"
    return RedirectResponse(url=github_release_url, status_code=302)


@router.post("/version", dependencies=[Depends(get_current_admin)])
def update_app_version(request: UpdateVersionRequest):
    """
    Allows the Admin (Sir) to publish a new app version notification.
    Requires Bearer token authentication.
    """
    data = _load_version_data()
    data["latest_version"] = request.latest_version
    data["version_code"] = request.version_code
    if request.min_supported_version is not None:
        data["min_supported_version"] = request.min_supported_version
    if request.apk_url is not None:
        data["apk_url"] = request.apk_url
    if request.release_notes is not None:
        data["release_notes"] = request.release_notes
    if request.force_update is not None:
        data["force_update"] = request.force_update
    data["published_at"] = datetime.now(timezone.utc).isoformat()

    _save_version_data(data)
    return {"message": "Version metadata updated successfully.", "data": data}
