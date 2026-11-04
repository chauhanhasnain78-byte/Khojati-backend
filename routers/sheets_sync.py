"""
Khojati Inventory Management System - Google Sheets Sync Router.

Endpoints for triggering and monitoring Google Sheets synchronisation.
"""

from fastapi import APIRouter, Depends

from auth import get_current_admin
from schemas import SyncStatusResponse, SyncTriggerResponse
from services.sheets_queue import sheets_queue

router = APIRouter(prefix="/api/sync", tags=["Sync"])


@router.post("/trigger", response_model=SyncTriggerResponse)
def trigger_sync(_admin: dict = Depends(get_current_admin)):
    """Manually trigger processing of the Google Sheets sync queue."""
    result = sheets_queue.process_queue()
    return SyncTriggerResponse(
        triggered=True,
        queue_length=result["remaining"],
        message=(
            f"Sync triggered. Processed: {result['processed']}, "
            f"Failed: {result['failed']}, Remaining: {result['remaining']}."
        ),
    )


@router.get("/status", response_model=SyncStatusResponse)
def get_sync_status():
    """Check the current status of the Google Sheets sync queue."""
    status = sheets_queue.get_status()
    return SyncStatusResponse(**status)
