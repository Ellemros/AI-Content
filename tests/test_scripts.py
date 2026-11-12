import pytest
from app.domain import CATEGORIES, LANGUAGES
from app.schemas.content import ScriptContent
from app.services.offline_scripts import build_offline_script
from pydantic import ValidationError


def _base(**overrides):
    content = build_offline_script("How machines learn", "ai", "en", "short")
    data = content.model_dump()
    data.update(overrides)
    return data


def test_offline_scripts_match_schema_for_both_languages():
    for language in LANGUAGES:
        for category in CATEGORIES:
            short = build_offline_script("How machines learn", category, language, "short")
            long = build_offline_script("How machines learn", category, language, "long")
            assert short.offline_draft is True
            assert long.offline_draft is True
            assert short.factual_claims
            assert all(claim.unverified and not claim.sources for claim in short.factual_claims)
            assert all(not claim.verified for claim in long.factual_claims)
            assert short.narration.startswith(short.hook)


def test_same_title_is_deterministic():
    first = build_offline_script("Signal and noise", "science", "cs", "short")
    second = build_offline_script("Signal and noise", "science", "cs", "short")
    assert first.narration == second.narration
    assert first.template_key == second.template_key


def test_unsourced_claim_is_rejected():
    data = _base(
        factual_claims=[
            {"text": "A specific made-up statistic.", "sources": [], "unverified": False, "verified": False}
        ]
    )
    with pytest.raises(ValidationError):
        ScriptContent.model_validate(data)


def test_verified_claim_needs_a_source():
    data = _base(
        factual_claims=[{"text": "A checked claim.", "sources": [], "verified": True, "unverified": False}]
    )
    with pytest.raises(ValidationError):
        ScriptContent.model_validate(data)


def test_long_hook_is_rejected():
    data = _base()
    data["hook"] = "This hook is intentionally much too long for a one to three second opening line."
    data["narration"] = data["hook"] + " " + data["narration"]
    with pytest.raises(ValidationError):
        ScriptContent.model_validate(data)
