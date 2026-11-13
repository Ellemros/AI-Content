"""In-process poller. Topic schedules stop before approval. Brand automation uses the same tick."""

import logging
import threading

from app.config import get_settings

logger = logging.getLogger("app.production")


class ProductionScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if get_settings().scheduler_poll_seconds <= 0:
            logger.info(
                "Scheduler poller is not started.",
                extra={"stage": "scheduler", "attempt": 1},
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="production-scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "Scheduler poller started.",
            extra={"stage": "scheduler", "attempt": 1},
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(max(get_settings().scheduler_poll_seconds, 1)):
            self.tick()

    def tick(self) -> None:
        from app.core.database import session_scope
        from app.services.schedule import run_due

        try:
            with session_scope() as session:
                try:
                    run_due(session)
                except Exception:
                    logger.exception(
                        "Topic schedule tick failed.",
                        extra={"stage": "scheduler", "attempt": 1},
                    )
                from app.services.automation import run_due_brands

                run_due_brands(session)
        except Exception:
            logger.exception("Scheduler tick failed.", extra={"stage": "scheduler", "attempt": 1})


scheduler = ProductionScheduler()


def start_scheduler() -> None:
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.stop()
