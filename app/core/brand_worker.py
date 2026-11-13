"""Optional poller. It stays off until BRAND_CYCLE_POLL_SECONDS is greater than zero."""

import logging
import threading
import time

from sqlalchemy import select

from app.config import get_settings
from app.core.database import session_scope
from app.core.seed import seed
from app.models import Brand
from app.services.business.cycle import run_brand_cycle

logger = logging.getLogger("app.business")
_started = False


def start_brand_worker() -> None:
    global _started
    seconds = get_settings().brand_cycle_poll_seconds
    if seconds <= 0 or _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(seconds,), name="brand-cycle", daemon=True).start()


def _loop(seconds: int) -> None:
    time.sleep(seconds)
    while True:
        try:
            with session_scope() as session:
                seed(session)
                brands = session.scalars(
                    select(Brand).where(Brand.status.in_(("experimental", "active")))
                ).all()
                for brand in brands:
                    run_brand_cycle(session, brand)
        except Exception:
            logger.exception("brand cycle poll failed")
        time.sleep(seconds)
