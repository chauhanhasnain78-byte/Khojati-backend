"""
Khojati Inventory Management System - Google Sheets Sync Queue.

Provides an in-memory deque-based queue for batching Google Sheets
sync operations with exponential backoff retry logic.

The actual Google Sheets API calls are currently stubbed out.  When the
service account JSON is configured, replace the ``_sync_to_sheets``
method with real gspread calls.
"""

import logging
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SheetsQueue:
    """
    Thread-safe in-memory queue for Google Sheets synchronisation tasks.

    Each queue entry is a dict with keys:
      - ``type``: "dispatch" | "inward" | "stock_update"
      - ``data``: The payload to sync.
      - ``created_at``: ISO timestamp.
      - ``retries``: Number of retry attempts so far.
    """

    MAX_RETRIES = 5
    BASE_BACKOFF_SECONDS = 2.0

    def __init__(self) -> None:
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = Lock()
        self._last_sync_at: Optional[str] = None

    @property
    def queue_length(self) -> int:
        """Return the current number of items in the queue."""
        with self._lock:
            return len(self._queue)

    @property
    def last_sync_at(self) -> Optional[str]:
        """Return the ISO timestamp of the last successful sync."""
        return self._last_sync_at

    def push_dispatch(self, order_data: dict) -> None:
        """
        Enqueue a dispatch order for Google Sheets synchronisation.

        Args:
            order_data: Dictionary containing order details.
        """
        with self._lock:
            self._queue.append({
                "type": "dispatch",
                "data": order_data,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "retries": 0,
            })
        logger.info("Dispatch order queued for Sheets sync: %s", order_data.get("order_number"))

    def push_inward(self, receipt_data: dict) -> None:
        """
        Enqueue an inward receipt for Google Sheets synchronisation.

        Args:
            receipt_data: Dictionary containing receipt details.
        """
        with self._lock:
            self._queue.append({
                "type": "inward",
                "data": receipt_data,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "retries": 0,
            })
        logger.info("Inward receipt queued for Sheets sync: %s", receipt_data.get("grn_number"))

    def push_stock_update(self) -> None:
        """Enqueue a generic stock update notification for Google Sheets."""
        with self._lock:
            self._queue.append({
                "type": "stock_update",
                "data": {"trigger": "stock_adjustment"},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "retries": 0,
            })
        logger.info("Stock update queued for Sheets sync.")

    def process_queue(self) -> dict:
        """
        Process all items in the queue with exponential backoff retry.

        Items that fail after MAX_RETRIES are discarded with a warning.

        Returns:
            Dictionary with processing results:
              - ``processed``: Number of successfully processed items.
              - ``failed``: Number of items that exhausted retries.
              - ``remaining``: Number of items still in the queue.
        """
        processed = 0
        failed = 0
        retry_items: list[dict] = []

        while True:
            with self._lock:
                if not self._queue:
                    break
                item = self._queue.popleft()

            success = self._sync_to_sheets(item)

            if success:
                processed += 1
                self._last_sync_at = datetime.now(timezone.utc).isoformat()
            else:
                item["retries"] += 1
                if item["retries"] < self.MAX_RETRIES:
                    # Apply exponential backoff.
                    backoff = self.BASE_BACKOFF_SECONDS * (2 ** (item["retries"] - 1))
                    logger.warning(
                        "Sheets sync failed for %s (attempt %d/%d). "
                        "Retrying in %.1f seconds.",
                        item["type"],
                        item["retries"],
                        self.MAX_RETRIES,
                        backoff,
                    )
                    time.sleep(min(backoff, 30.0))  # Cap at 30 seconds.
                    retry_items.append(item)
                else:
                    failed += 1
                    logger.error(
                        "Sheets sync permanently failed for %s after %d attempts. "
                        "Item discarded.",
                        item["type"],
                        self.MAX_RETRIES,
                    )

        # Re-enqueue items that need retry.
        if retry_items:
            with self._lock:
                for item in retry_items:
                    self._queue.appendleft(item)

        return {
            "processed": processed,
            "failed": failed,
            "remaining": self.queue_length,
        }

    def _sync_to_sheets(self, item: dict) -> bool:
        """
        Stub for Google Sheets API synchronisation.

        Replace this method with actual gspread calls once the service
        account JSON file is configured in ``config.py``.

        Args:
            item: Queue entry with ``type`` and ``data``.

        Returns:
            True if the sync succeeded, False on failure.
        """
        sync_type = item.get("type", "unknown")
        data = item.get("data", {})

        # Stub implementation — log and succeed.
        logger.info(
            "Would sync to Google Sheets: type=%s, data=%s",
            sync_type,
            data,
        )
        return True

    def get_status(self) -> dict:
        """
        Return the current status of the sync queue.

        Returns:
            Dictionary with queue length, last sync time, and
            configuration status.
        """
        from config import settings

        is_configured = bool(
            settings.GOOGLE_SHEETS_ID and settings.GOOGLE_SERVICE_ACCOUNT_FILE
        )

        return {
            "queue_length": self.queue_length,
            "last_sync_at": self._last_sync_at,
            "is_configured": is_configured,
            "message": (
                "Google Sheets sync is active."
                if is_configured
                else "Google Sheets is not configured. Set GOOGLE_SHEETS_ID and "
                     "GOOGLE_SERVICE_ACCOUNT_FILE in config.py to enable sync."
            ),
        }


# Module-level singleton instance.
sheets_queue = SheetsQueue()
