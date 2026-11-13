"""Partial opportunity scores. Missing factors stay out of the total and are not rescaled."""

from decimal import Decimal

from app.config import get_settings

FACTORS = (
    "monetization_potential",
    "audience_demand",
    "competition_opportunity",
    "content_scalability",
    "affiliate_sponsorship",
    "production_cost_efficiency",
    "evergreen",
)


def configured_weights() -> dict[str, Decimal]:
    settings = get_settings()
    raw = {
        "monetization_potential": settings.opportunity_weight_monetization,
        "audience_demand": settings.opportunity_weight_demand,
        "competition_opportunity": settings.opportunity_weight_competition,
        "content_scalability": settings.opportunity_weight_scalability,
        "affiliate_sponsorship": settings.opportunity_weight_affiliate,
        "production_cost_efficiency": settings.opportunity_weight_cost,
        "evergreen": settings.opportunity_weight_evergreen,
    }
    return {key: Decimal(str(value)) for key, value in raw.items()}


def score_signals(signals: dict[str, dict], weights: dict[str, Decimal]) -> dict:
    """Sum weight times value for known factors only. Do not divide by coverage."""
    used = Decimal("0")
    coverage = Decimal("0")
    for factor, weight in weights.items():
        value = (signals.get(factor) or {}).get("value")
        if value is None:
            continue
        used += weight * Decimal(str(value))
        coverage += weight
    total = sum(weights.values(), Decimal("0"))
    return {
        "partial_score": used.quantize(Decimal("0.01")),
        "coverage": coverage.quantize(Decimal("0.0001")),
        "weight_total": total.quantize(Decimal("0.0001")),
        "complete": coverage == total and total > 0,
    }
