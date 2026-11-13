"""Business monitor. The cycle scores opportunities. It does not render or upload."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import AppError, NotFoundError
from app.models import (
    AffiliateClick,
    AffiliateProduct,
    AffiliateProgram,
    Brand,
    ContentOpportunity,
    CycleRun,
    Sponsorship,
)
from app.services.business.cycle import disclosure_for, run_brand_cycle
from app.services.business.records import save_product, save_program, save_sponsorship
from app.services.cost import month_spend
from app.web.views import _redirect, _render

router = APIRouter()


def _brand(session: Session, brand_id: str) -> Brand:
    brand = session.get(Brand, brand_id)
    if brand is None:
        raise NotFoundError("Brand was not found.")
    return brand


@router.get("/business")
def business_page(request: Request, session: Session = Depends(get_db)):
    brands = session.scalars(select(Brand).order_by(Brand.name)).all()
    opportunities = session.scalars(
        select(ContentOpportunity).order_by(ContentOpportunity.updated_at.desc())
    ).all()
    runs = session.scalars(select(CycleRun).order_by(CycleRun.started_at.desc())).all()
    programs = session.scalars(select(AffiliateProgram).order_by(AffiliateProgram.name)).all()
    products = session.scalars(select(AffiliateProduct).order_by(AffiliateProduct.name)).all()
    clicks = dict(
        session.execute(
            select(AffiliateClick.product_id, func.count()).group_by(AffiliateClick.product_id)
        ).all()
    )
    sponsors = session.scalars(select(Sponsorship).order_by(Sponsorship.created_at.desc())).all()
    return _render(
        request,
        "business.html",
        brands=brands,
        opportunities=opportunities[:40],
        runs=runs[:8],
        programs=programs,
        products=products,
        clicks=clicks,
        sponsors=sponsors,
        spend=month_spend(session),
        disclosures={program.id: disclosure_for(program) for program in programs},
    )


@router.post("/business/cycle")
def business_cycle(slug: str = Form(""), session: Session = Depends(get_db)):
    query = select(Brand)
    if slug.strip():
        query = query.where(Brand.slug == slug.strip())
    brands = session.scalars(query).all()
    if not brands:
        return _redirect("/business", error="No brand matched.")
    for brand in brands:
        run_brand_cycle(session, brand)
    return _redirect("/business", message="Cycle finished. No video was rendered and nothing was uploaded.")


@router.post("/business/brands/{brand_id}/pause")
def pause_brand(brand_id: str, session: Session = Depends(get_db)):
    brand = _brand(session, brand_id)
    brand.status = "experimental" if brand.status == "paused" else "paused"
    label = "paused" if brand.status == "paused" else "experimental"
    return _redirect("/business", message=f"{brand.name} is {label}.")


@router.post("/business/brands/{brand_id}/programs")
def add_program(
    brand_id: str,
    name: str = Form(...),
    provider: str = Form(""),
    locale: str = Form(""),
    status: str = Form("draft"),
    commission_type: str = Form(""),
    commission_rate: str = Form(""),
    terms: str = Form(""),
    expires_on: str = Form(""),
    session: Session = Depends(get_db),
):
    brand = _brand(session, brand_id)
    try:
        save_program(
            session,
            brand,
            name=name,
            provider=provider,
            locale=locale,
            status=status,
            commission_type=commission_type,
            commission_rate=commission_rate,
            terms=terms,
            expires_on=expires_on,
        )
    except AppError as exc:
        return _redirect("/business", error=exc.message)
    return _redirect("/business", message="Affiliate program stored. It is not inserted into a video.")


@router.post("/business/programs/{program_id}/products")
def add_product(
    program_id: str,
    name: str = Form(...),
    category: str = Form(""),
    product_url: str = Form(...),
    affiliate_url: str = Form(...),
    session: Session = Depends(get_db),
):
    program = session.get(AffiliateProgram, program_id)
    if program is None:
        raise NotFoundError("Affiliate program was not found.")
    try:
        save_product(
            session,
            program,
            name=name,
            category=category,
            product_url=product_url,
            affiliate_url=affiliate_url,
        )
    except AppError as exc:
        return _redirect("/business", error=exc.message)
    return _redirect("/business", message="Affiliate product stored.")


@router.post("/business/brands/{brand_id}/sponsorships")
def add_sponsorship(
    brand_id: str,
    sponsor_name: str = Form(...),
    status: str = Form("lead"),
    rate: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_db),
):
    brand = _brand(session, brand_id)
    try:
        save_sponsorship(session, brand, sponsor_name=sponsor_name, status=status, rate=rate, notes=notes)
    except AppError as exc:
        return _redirect("/business", error=exc.message)
    return _redirect("/business", message="Sponsorship stored. A lead is not an agreement.")


@router.get("/go/{product_id}")
def affiliate_redirect(product_id: str, session: Session = Depends(get_db)):
    product = session.get(AffiliateProduct, product_id)
    program = session.get(AffiliateProgram, product.program_id) if product is not None else None
    if product is None or program is None or program.status != "active" or not product.active:
        raise NotFoundError("That affiliate link is not active.")
    session.add(AffiliateClick(product_id=product.id))
    return RedirectResponse(product.affiliate_url, status_code=302)
