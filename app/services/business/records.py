"""Affiliate programs and sponsorships. Rates exist only when someone enters them."""

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AffiliateProduct, AffiliateProgram, Brand, Sponsorship


def _rate(raw: str) -> Decimal | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise AppError(
            "Commission rate must be a number you copied from the program, or left empty."
        ) from exc


def save_program(
    session: Session,
    brand: Brand,
    *,
    name: str,
    provider: str,
    locale: str,
    status: str,
    commission_type: str,
    commission_rate: str,
    terms: str,
    expires_on: str,
) -> AffiliateProgram:
    if status not in {"draft", "active", "paused"}:
        raise AppError("Program status must be draft, active, or paused.")
    rate = _rate(commission_rate)
    if status == "active" and rate is None:
        raise AppError("An active program needs the commission rate from that program. None was entered.")
    row = AffiliateProgram(
        brand_id=brand.id,
        name=name.strip()[:160],
        provider=provider.strip()[:160],
        locale=locale.strip()[:16],
        status=status,
        commission_type=commission_type.strip()[:40],
        commission_rate=rate,
        terms=terms.strip(),
        expires_on=expires_on.strip()[:32],
    )
    session.add(row)
    session.flush()
    return row


def save_product(
    session: Session,
    program: AffiliateProgram,
    *,
    name: str,
    category: str,
    product_url: str,
    affiliate_url: str,
) -> AffiliateProduct:
    link = affiliate_url.strip()
    page = product_url.strip()
    if not link.startswith("https://") or not page.startswith("https://"):
        raise AppError("Product and affiliate URLs must be https links from the program.")
    row = AffiliateProduct(
        program_id=program.id,
        name=name.strip()[:200],
        category=category.strip()[:80],
        product_url=page,
        affiliate_url=link,
        active=True,
    )
    session.add(row)
    session.flush()
    return row


def save_sponsorship(
    session: Session,
    brand: Brand,
    *,
    sponsor_name: str,
    status: str,
    rate: str,
    notes: str,
) -> Sponsorship:
    if status not in {"lead", "agreed", "declined"}:
        raise AppError("Sponsorship status must be lead, agreed, or declined.")
    amount = _rate(rate)
    if status == "agreed" and amount is None:
        raise AppError(
            "An agreed sponsorship needs the rate from that agreement. A lead is not an agreement."
        )
    row = Sponsorship(
        brand_id=brand.id,
        sponsor_name=sponsor_name.strip()[:160],
        status=status,
        rate=amount,
        notes=notes.strip(),
    )
    session.add(row)
    session.flush()
    return row
