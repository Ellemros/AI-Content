"""Signals we can actually support. Unknown factors stay null."""

import re
from decimal import Decimal

from app.models import AffiliateProgram, Brand
from app.services.research.scoring import classify_horizon
from app.services.textutil import tokens

_MEDICAL = re.compile(
    # Conservative on purpose: a false hold costs one topic, a missed one risks medical advice.
    r"\b(treatments?|diagnosis|diagnosed|symptoms?|diseases?|alzheimer\S*|cancers?|medications?|"
    r"therap(?:y|ies)|depression|anxiety|dementia|stroke|addiction|obesity|vaccines?|drugs?|disorders?|"
    r"cures?|clinical|patients?|pain|diets?|supplements?|diabetes|surgery|mental health|ptsd|parkinson\S*|"
    r"tumou?rs?|infections?|virus|blood pressure|cholesterol|overdose|suicide|cannabis|marijuana)\b",
    re.IGNORECASE,
)
_FINANCIAL = re.compile(
    r"\b(stock tip|guaranteed return|invest in|crypto signal|buy this stock)\b",
    re.IGNORECASE,
)


def unknown(note: str) -> dict:
    return {"value": None, "kind": "unknown", "note": note}


def hold_reason(title: str, summary: str, brand: Brand) -> str:
    blocked = set((brand.identity or {}).get("blocked_areas") or [])
    text = f"{title} {summary}"
    if "medical_advice" in blocked and _MEDICAL.search(text):
        return "Held. The title looks like medical advice, which this brand does not automate."
    if "financial_advice" in blocked and _FINANCIAL.search(text):
        return "Held. The title looks like financial advice, which this brand does not automate."
    return ""


def build_signals(
    *,
    title: str,
    summary: str,
    brand: Brand,
    programs: list[AffiliateProgram],
) -> dict[str, dict]:
    signals = {
        factor: unknown("No source is configured for this factor.")
        for factor in (
            "monetization_potential",
            "audience_demand",
            "competition_opportunity",
            "content_scalability",
            "affiliate_sponsorship",
            "production_cost_efficiency",
            "evergreen",
        )
    }
    horizon = classify_horizon(title, summary)
    if horizon == "evergreen":
        signals["evergreen"] = {
            "value": 80,
            "kind": "assumption",
            "note": "Keyword heuristic. Not a traffic or revenue measurement.",
        }
    elif horizon == "trending":
        signals["evergreen"] = {
            "value": 25,
            "kind": "assumption",
            "note": "Announcement language. Not a traffic or revenue measurement.",
        }
    terms = {str(term) for term in (brand.identity or {}).get("terms") or []}
    overlap = tokens(f"{title} {summary}") & terms
    if len(overlap) >= 2:
        shown = ", ".join(sorted(overlap)[:6])
        signals["content_scalability"] = {
            "value": 70,
            "kind": "assumption",
            "note": f"Shares brand vocabulary ({shown}). Not an audience count.",
        }
    elif len(overlap) == 1:
        signals["content_scalability"] = {
            "value": 40,
            "kind": "assumption",
            "note": "One brand term. Not an audience count.",
        }
    match = _matching_program(title, summary, programs)
    if match is not None:
        if match.commission_rate is None:
            rate = "no rate stored"
        else:
            rate = f"stored rate {Decimal(str(match.commission_rate))}"
        signals["affiliate_sponsorship"] = {
            "value": None,
            "kind": "measured",
            "note": (
                f"Configured program {match.name} ({match.locale or 'locale unset'}) {rate}. "
                "Conversions are not measured, so this factor stays out of the score."
            ),
        }
    signals["audience_demand"]["note"] = (
        "No search-volume source is connected. A YouTube sample, when present, is stored separately."
    )
    signals["competition_opportunity"]["note"] = "No competitor-count source is connected."
    signals["monetization_potential"]["note"] = (
        "Advertising revenue is not available. RPM is not estimated."
    )
    signals["production_cost_efficiency"]["note"] = "No cost-per-video measurement is stored yet."
    return signals


def _matching_program(
    title: str,
    summary: str,
    programs: list[AffiliateProgram],
) -> AffiliateProgram | None:
    words = tokens(f"{title} {summary}")
    for program in programs:
        if program.status != "active":
            continue
        name_words = tokens(program.name)
        if name_words and name_words & words:
            return program
    return None
