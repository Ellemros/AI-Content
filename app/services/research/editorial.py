"""Editorial plan and originality check. A low score rejects the script. It does not rewrite it."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EditorialReview, Script, ScriptVersion, Topic
from app.services.scripts import current_version
from app.services.textutil import fold, sequence_ratio, split_sentences, tokens, words

_GENERIC = (
    "in today's fast-paced",
    "in todays fast-paced",
    "let's dive in",
    "lets dive in",
    "buckle up",
    "game-changer",
    "game changer",
    "unlock your potential",
    "in this video we will",
    "it's important to note",
    "it is important to note",
    "without further ado",
    "ever-evolving landscape",
    "delve",
    "v dnešní uspěchané době",
    "pojďme se ponořit",
    "je důležité poznamenat",
)
_PROMO = (
    "buy now",
    "link in bio",
    "use my code",
    "don't miss this deal",
    "limited time offer",
    "smash that like",
    "kup teď",
    "odkaz v biu",
)
_SPECULATION = ("might", "could", "may", "predict", "someday", "possibly", "maybe")
_SPECIFIC_CLAIM = re.compile(
    r"\d|%|\bpercent\b|study shows|researchers found|according to|scientists say",
    re.IGNORECASE,
)
_SPECIFIC_NARRATION = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|\bpercent\b)|\b(?:19|20)\d{2}\b|"
    r"study shows|researchers found|according to|scientists say",
    re.IGNORECASE,
)
_OPENING_RATIO = 0.88
_CLAIM_JACCARD = 0.82


def _contains(text: str, phrase: str) -> bool:
    raw = text.casefold()
    folded = fold(text)
    folded_phrase = fold(phrase)
    if " " in phrase or "-" in phrase:
        return phrase.casefold() in raw or (folded_phrase != "" and folded_phrase in folded)
    return re.search(rf"\b{re.escape(phrase.casefold())}\b", raw) is not None or re.search(
        rf"\b{re.escape(folded_phrase)}\b", folded
    ) is not None


def _jaccard_text(left: str, right: str) -> float:
    a = tokens(left)
    b = tokens(right)
    if not a or not b:
        return 1.0 if fold(left) == fold(right) else 0.0
    return len(a & b) / len(a | b)


def _ending(narration: str) -> str:
    sentences = split_sentences(narration)
    return sentences[-1] if sentences else ""


def _clamp(value: int) -> int:
    return max(0, min(100, value))


def review_content(
    *,
    title: str,
    hook: str,
    narration: str,
    scenes: list[str],
    claims: list[dict],
    cta: str,
    audience: str,
    prior_hooks: list[str],
    prior_claims: list[str],
    prior_endings: list[str],
    threshold: int,
) -> dict:
    """Score a script. Confirmed facts need a source. Feed text is never added here."""
    issues: list[dict] = []
    spoken = f"{hook}\n{narration}\n{cta}"
    generic_hits = [phrase for phrase in _GENERIC if _contains(spoken, phrase)]
    for phrase in generic_hits:
        issues.append({"kind": "generic_phrase", "detail": f"Generic phrasing: {phrase}."})
    if len(generic_hits) >= 2:
        issues.append({"kind": "filler", "detail": "Several generic phrases are doing the work of a point."})
    for phrase in _PROMO:
        if _contains(spoken, phrase):
            issues.append({"kind": "promo_language", "detail": f"Promotional phrasing: {phrase}."})
            break
    if len(tokens(title)) < 2:
        issues.append({"kind": "weak_question", "detail": "The title does not name a specific subject."})

    evidence: list[dict] = []
    confirmed = False
    for claim in claims:
        text = str(claim.get("text", "")).strip()
        sources = [str(item) for item in claim.get("sources", []) if str(item).startswith(("http://", "https://"))]
        verified = bool(claim.get("verified")) and bool(sources)
        speculative = any(_contains(text, word) for word in _SPECULATION)
        if verified and not speculative:
            kind = "confirmed"
            confirmed = True
        elif speculative:
            kind = "speculation"
        else:
            kind = "unverified"
        evidence.append({"text": text, "kind": kind, "sources": sources})
        specific = bool(_SPECIFIC_CLAIM.search(text))
        if specific and not verified:
            issues.append(
                {
                    "kind": "unsupported_claim",
                    "detail": "A specific claim has no source URL. It cannot be spoken as fact.",
                }
            )
        if verified and speculative:
            issues.append(
                {
                    "kind": "speculation_as_fact",
                    "detail": "A prediction is marked confirmed. Label it as speculation.",
                }
            )
    if _SPECIFIC_NARRATION.search(narration) and not confirmed:
        issues.append(
            {
                "kind": "unsupported_claim",
                "detail": "The narration states a figure or attribution without a sourced claim.",
            }
        )

    opening_hit = ""
    for other in prior_hooks:
        if other and sequence_ratio(hook, other) >= _OPENING_RATIO:
            opening_hit = other
            break
    if opening_hit:
        issues.append({"kind": "repetitive_opening", "detail": "The opening matches an earlier script."})
    for other in prior_claims:
        claim_texts = [str(claim.get("text", "")) for claim in claims if claim.get("text")]
        if any(_jaccard_text(text, other) >= _CLAIM_JACCARD for text in claim_texts):
            issues.append({"kind": "repeated_fact", "detail": "A claim repeats an earlier script."})
            break
    ending = _ending(narration)
    for other in prior_endings:
        if other and sequence_ratio(ending, other) >= _OPENING_RATIO:
            issues.append(
                {"kind": "repeated_conclusion", "detail": "The conclusion matches an earlier script."}
            )
            break
    if len(words(ending)) < 4:
        issues.append(
            {"kind": "missing_takeaway", "detail": "The closing sentence does not carry a takeaway."}
        )

    penalty = 0
    originality_penalty = 0
    for issue in issues:
        kind = issue["kind"]
        if kind == "generic_phrase":
            penalty += 12
            originality_penalty += 12
        elif kind == "filler":
            penalty += 10
            originality_penalty += 10
        elif kind == "promo_language":
            penalty += 25
        elif kind == "unsupported_claim":
            penalty += 20
        elif kind == "speculation_as_fact":
            penalty += 15
        elif kind == "repetitive_opening":
            penalty += 18
            originality_penalty += 18
        elif kind == "repeated_fact":
            penalty += 15
            originality_penalty += 15
        elif kind == "repeated_conclusion":
            penalty += 15
            originality_penalty += 15
        elif kind in {"weak_question", "missing_takeaway"}:
            penalty += 10
    quality = _clamp(100 - penalty)
    originality = _clamp(100 - originality_penalty)
    unsupported = any(issue["kind"] == "unsupported_claim" for issue in issues)
    subject = title.strip().rstrip(".") or "this subject"
    takeaway = ending if len(words(ending)) >= 4 else "The draft has no explicit takeaway yet."
    return {
        "audience": audience,
        "central_question": f"What is worth understanding about {subject}?",
        "takeaway": takeaway,
        "opening": hook.strip(),
        "narrative": [scene for scene in scenes if scene.strip()],
        "evidence": evidence,
        "conclusion": ending or cta.strip(),
        "quality_score": quality,
        "originality_score": originality,
        "issues": issues,
        "passed": quality >= threshold and not unsupported,
        "threshold": threshold,
    }


def _priors(session: Session, version_id: str) -> tuple[list[str], list[str], list[str]]:
    rows = session.scalars(select(ScriptVersion).where(ScriptVersion.id != version_id)).all()
    hooks: list[str] = []
    claims: list[str] = []
    endings: list[str] = []
    for row in rows[-300:]:
        hooks.append(row.hook or "")
        endings.append(_ending(row.narration or ""))
        for claim in row.factual_claims or []:
            text = str(claim.get("text", "")).strip()
            if text:
                claims.append(text)
    return hooks, claims, endings


def review_script(session: Session, script: Script) -> EditorialReview:
    version = current_version(session, script)
    topic = session.get(Topic, script.topic_id)
    audience = topic.target_audience if topic is not None and topic.target_audience else "curious adults"
    hooks, claims, endings = _priors(session, version.id)
    scenes = [str(scene.get("narration", "")) for scene in version.scenes or []]
    result = review_content(
        title=version.title,
        hook=version.hook,
        narration=version.narration,
        scenes=scenes,
        claims=list(version.factual_claims or []),
        cta=version.cta,
        audience=audience,
        prior_hooks=hooks,
        prior_claims=claims,
        prior_endings=endings,
        threshold=get_settings().editorial_quality_minimum,
    )
    review = EditorialReview(
        script_id=script.id,
        script_version_id=version.id,
        topic_id=script.topic_id,
        audience=result["audience"],
        central_question=result["central_question"],
        takeaway=result["takeaway"],
        opening=result["opening"],
        narrative=result["narrative"],
        evidence=result["evidence"],
        conclusion=result["conclusion"],
        quality_score=result["quality_score"],
        originality_score=result["originality_score"],
        issues=result["issues"],
        passed=result["passed"],
        threshold=result["threshold"],
    )
    session.add(review)
    session.flush()
    return review
