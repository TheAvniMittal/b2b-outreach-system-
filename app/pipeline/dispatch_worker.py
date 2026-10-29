"""
Web Dispatch Worker for Narayani Outreach Pipeline (Phase 5).
Monitors sending state and continuously claims and processes eligible outbound queue items
when sending is active.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db_session
from app.pipeline.sending_controller import is_sending_active
from app.pipeline.providers.base import EmailProvider
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.pipeline.providers.smtp_provider import SMTPEmailProvider
from app.pipeline.runner import run_single_pipeline_item, run_pipeline_batch

logger = logging.getLogger("dispatch_worker")


def get_default_provider() -> EmailProvider:
    """Returns active email provider: SMTPEmailProvider if live sending enabled, else MockEmailProvider."""
    if settings.live_email_sending:
        return SMTPEmailProvider()
    return MockEmailProvider()


class DispatchWorkerManager:
    """
    Background worker loop that claims and executes eligible tasks from outbound_queue
    as long as sending is globally active and auto-stop has not triggered.
    """

    def __init__(self, poll_interval_seconds: float = 3.0):
        self.poll_interval = poll_interval_seconds
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_processing = False
        self._last_run_at: Optional[datetime] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._items_processed_session = 0

    def start(self):
        """Starts background dispatch worker thread if not already alive."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="OutboundDispatchWorker"
        )
        self._thread.start()
        logger.info("Outbound dispatch worker started.")

    def stop(self):
        """Signals background worker to stop and waits for termination."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=4.0)
            self._thread = None
        logger.info("Outbound dispatch worker stopped.")

    def wake(self):
        """Wakes up worker immediately without waiting for poll interval."""
        self._wake_event.set()

    def dispatch_single_now(
        self, session: Session, worker_id: str = "web_dispatcher_manual"
    ) -> Optional[Dict[str, Any]]:
        """Manually dispatches a single eligible item synchronously."""
        provider = get_default_provider()
        self._is_processing = True
        try:
            res = run_single_pipeline_item(session, worker_id=worker_id, provider=provider)
            self._last_run_at = datetime.now(timezone.utc)
            self._last_result = res
            if res:
                self._items_processed_session += 1
            return res
        finally:
            self._is_processing = False

    def dispatch_batch_now(
        self, session: Session, limit: int = 5, worker_id: str = "web_dispatcher_manual"
    ) -> List[Dict[str, Any]]:
        """Manually dispatches up to limit eligible items synchronously."""
        provider = get_default_provider()
        self._is_processing = True
        try:
            results = run_pipeline_batch(
                session, worker_id=worker_id, provider=provider, limit=limit
            )
            self._last_run_at = datetime.now(timezone.utc)
            if results:
                self._last_result = results[-1]
                self._items_processed_session += len(results)
            return results
        finally:
            self._is_processing = False

    def get_status(self) -> Dict[str, Any]:
        """Returns live worker health and processing metrics."""
        is_alive = self._thread is not None and self._thread.is_alive()
        provider = get_default_provider()
        return {
            "worker_alive": is_alive,
            "is_processing": self._is_processing,
            "live_email_sending": settings.live_email_sending,
            "provider_class": provider.__class__.__name__,
            "items_processed_session": self._items_processed_session,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_result": self._last_result,
        }

    def _run_loop(self):
        """Continuous worker execution loop."""
        logger.info("Outbound dispatch worker loop running.")
        while not self._stop_event.is_set():
            try:
                with get_db_session() as session:
                    active, reason = is_sending_active(session)
                    if not active:
                        # Wait for wake event or timeout before checking again
                        self._wake_event.wait(timeout=self.poll_interval)
                        self._wake_event.clear()
                        continue

                    # Sending is active: attempt to claim and process 1 item
                    self._is_processing = True
                    provider = get_default_provider()
                    result = run_single_pipeline_item(
                        session, worker_id="web_dispatcher", provider=provider
                    )
                    self._last_run_at = datetime.now(timezone.utc)
                    self._last_result = result

                    if result is not None:
                        self._items_processed_session += 1
                        logger.info(f"Dispatch worker processed task: {result}")
                        # Short throttle delay between consecutive items
                        time.sleep(1.0)
                    else:
                        # No task was eligible: wait for next poll interval or wake
                        self._wake_event.wait(timeout=self.poll_interval)
                        self._wake_event.clear()

            except Exception as e:
                logger.error(f"Error in dispatch worker loop: {e}", exc_info=True)
                time.sleep(self.poll_interval)
            finally:
                self._is_processing = False


dispatch_worker = DispatchWorkerManager()
