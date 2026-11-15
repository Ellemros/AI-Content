"""The cheap model steps (topic ranking, hook choice, fact check, story review) go through here.

Order: Gemini when CHEAP_MODEL_PROVIDER=gemini and a key is set, otherwise or on any Gemini
failure (quota, outage, bad JSON) the OpenAI check model. The step never fails just because the
free provider ran out; it fails only if neither provider can answer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.models import APIUsage, ProductionJob


def cheap_available() -> bool:
    settings = get_settings()
    gemini = settings.cheap_model_provider == "gemini" and bool(settings.gemini_api_key)
    return gemini or (settings.openai_enabled() and bool(settings.openai_factcheck_model))


def cheap_json(
    session: Session, job: ProductionJob | None, *, operation: str, system: str, user: str
) -> dict:
    """JSON from the cheapest available model. Records the cost on the job when there is one."""
    from app.providers.gemini_provider import GeminiProvider, GeminiUnavailable
    from app.providers.openai_provider import OpenAIProvider
    from app.services.production.budget import add_cost, assert_production_budget

    settings = get_settings()
    attempts: list[str] = []
    if settings.cheap_model_provider == "gemini" and settings.gemini_api_key:
        gemini = GeminiProvider(session)
        try:
            result = gemini.complete_json(operation=operation, system=system, user=user)
            _charge(session, job, "gemini", operation, add_cost)
            if job is not None:
                _note(job, operation, f"gemini:{gemini.model}")
            return result
        except GeminiUnavailable as exc:
            attempts.append(exc.message)
    if not (settings.openai_enabled() and settings.openai_factcheck_model):
        raise AppError("No model is available for this step. " + " ".join(attempts))
    provider = OpenAIProvider(session, model=settings.openai_factcheck_model)
    if job is not None:
        assert_production_budget(session, job, provider._estimate(system, user))
    result = provider.complete_json(operation=operation, system=system, user=user)
    _charge(session, job, "openai", operation, add_cost)
    if job is not None:
        _note(job, operation, f"openai:{provider.model}" + (" (fallback)" if attempts else ""))
    return result


def _charge(session: Session, job: ProductionJob | None, provider: str, operation: str, add_cost) -> None:
    if job is None:
        return
    usage = session.scalar(
        select(APIUsage)
        .where(APIUsage.operation == operation, APIUsage.provider == provider)
        .order_by(APIUsage.created_at.desc())
    )
    if usage is not None:
        add_cost(
            session,
            job,
            provider=provider,
            operation=operation,
            cost=usage.estimated_cost_usd,
            cached=usage.cached,
        )


def _note(job: ProductionJob, operation: str, used: str) -> None:
    report = dict(job.report or {})
    models = dict(report.get("check_models") or {})
    models[operation] = used
    report["check_models"] = models
    job.report = report
