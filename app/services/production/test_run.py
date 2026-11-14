"""One explicit production test. It does not discover feeds and it does not publish.

The opportunity it creates uses status ``test``. Normal automation only selects
``evaluated`` rows, and a test success does not count toward the daily video cap.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.models import Brand, ContentOpportunity, DiscoveredItem, ResearchSource
from app.services.business.signals import hold_reason
from app.services.production.extract import extract_article
from app.services.production.pipeline import _brand_voice, produce_next
from app.services.voice import plan_engine

TEST_TITLE = "The default option shapes an everyday choice"
TEST_SUMMARY = (
    "People often keep the option that was already selected when they plan a route "
    "or decide when to start a habit."
)
TEST_URL = "https://production-test.invalid/everyday-default-choice"
TEST_HTML = """
<html><body>
<article>
<h1>The default option shapes an everyday choice</h1>
<p>People often keep the option that was already selected when they plan a route home.</p>
<p>The same pull shows up when someone picks a time to start a daily habit.</p>
<p>A short pause before the choice makes the default easier to notice.</p>
<p>Writing the alternative down turns an automatic yes into a comparison.</p>
<p>Checking the default is a habit for ordinary decisions about time and attention.</p>
</article>
</body></html>
"""


def topic_hold(brand: Brand) -> str:
    """The same medical and financial hold used by discovery, applied to this fixed topic."""
    extracted = extract_article(TEST_HTML)
    return hold_reason(TEST_TITLE, f"{TEST_SUMMARY}\n{extracted}", brand)


def fetch_test_article(url: str) -> tuple[int, str]:
    if url != TEST_URL:
        raise AppError("The production test only retrieves its own fixed article.")
    return 200, TEST_HTML


def _require_live_providers(brand: Brand) -> None:
    settings = get_settings()
    if not settings.openai_enabled():
        raise AppError("OPENAI_API_KEY is empty. The test was not started and nothing was published.")
    if plan_engine(settings) != "elevenlabs" or not settings.elevenlabs_enabled():
        raise AppError(
            "ElevenLabs is not the active voice. The test was not started and nothing was published."
        )
    language = brand.language if brand.language in {"en", "cs"} else "en"
    if not _brand_voice(brand, language):
        raise AppError("No ElevenLabs voice id is set. The test was not started and nothing was published.")


def _test_source(session: Session) -> ResearchSource:
    source = session.scalar(select(ResearchSource).where(ResearchSource.url == TEST_URL))
    if source is None:
        source = ResearchSource(
            name="Production test article",
            kind="fixture",
            url=TEST_URL,
            category="facts",
            enabled=False,
        )
        session.add(source)
        session.flush()
    source.enabled = False
    return source


def run_test_production(session: Session, brand: Brand, *, require_live_providers: bool = False):
    """Render one fixed non-medical topic through the normal pipeline.

    Existing opportunity status and hold_reason values are not updated. The health
    hold is applied to the test topic before any row is created. Nothing is published.
    """
    reason = topic_hold(brand)
    if reason:
        raise AppError(f"{reason} The test did not start and no opportunity was changed.")
    if require_live_providers:
        _require_live_providers(brand)
    source = _test_source(session)
    external_id = uuid.uuid4().hex
    item = DiscoveredItem(
        source_id=source.id,
        external_id=external_id,
        title=TEST_TITLE[:300],
        summary=TEST_SUMMARY,
        url=TEST_URL,
        category="facts",
        fingerprint=hashlib.sha256(f"{TEST_URL}:{external_id}".encode()).hexdigest(),
        status="test",
        source_verified=True,
        source_status="200",
    )
    session.add(item)
    session.flush()
    opportunity = ContentOpportunity(
        brand_id=brand.id,
        discovered_item_id=item.id,
        title=TEST_TITLE[:300],
        status="test",
        hold_reason="",
        production_status="blocked",
        signals=[],
    )
    session.add(opportunity)
    session.flush()
    return produce_next(
        session,
        brand,
        opportunity_id=opportunity.id,
        fetch=fetch_test_article,
        allow_placeholder=False,
        test_run=True,
    )
