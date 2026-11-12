"""Exponential backoff for provider calls and flaky media commands."""

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Raised when a call may succeed if it is tried again."""


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    retries: int,
    base_delay: float,
    retry_on: tuple[type[Exception], ...] = (RetryableError,),
) -> T:
    attempts = max(retries, 1)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt >= attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "retrying after error",
                extra={"attempt": attempt, "delay_s": round(delay, 3), "error": str(exc)[:300]},
            )
            time.sleep(delay)
    raise RuntimeError("retry loop exited without a result")
