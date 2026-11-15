"""Topic research agent: proposes new backlog topics and backs each one with real, fetchable sources.

1. A cheap model proposes concepts that fit the brand (behavioural-economics findings, money
   psychology), each with a viewer question, the Wikipedia article title, and a search phrase for
   the original paper. Topics already in the backlog or already produced are listed so it avoids them.
2. Nothing the model says about sources is trusted. Each topic is checked against official APIs:
   - the Wikipedia article must exist (REST summary, not a disambiguation page);
   - OpenAlex (open scholarly index) must find an open-access paper whose title matches the concept;
   - every source must actually be fetchable and yield enough article text through the same polite
     fetcher the pipeline uses (sites that refuse bots or show a CAPTCHA are skipped).
3. Medical and financial-advice holds apply. Near-duplicates are dropped.
4. Survivors are appended to the topic backlog JSON with how they were checked.

The model only suggests; the checks decide. A topic without a usable source is never added.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import utc_now
from app.models import Brand, ContentOpportunity
from app.services.research import dossier
from app.services.textutil import fold, jaccard, tokens


def tokens_list(text: str) -> list[str]:
    """Every word occurrence, normalised the same way as tokens() (which returns a set)."""
    return fold(str(text)).split()


OPENALEX = "https://api.openalex.org/works"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MIN_CITATIONS = 30
_GENERIC = {"effect", "bias", "rule", "theory", "law", "paradox", "the", "of", "and", "mindset", "principle"}


@dataclass
class Candidate:
    title: str
    question: str
    concept: str
    wikipedia: str
    paper_query: str
    structure: str = ""
    theme: str = ""
    sources: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    rejected: str = ""


def _get(url: str, params: dict | None = None) -> httpx.Response:
    return httpx.get(
        url, params=params, headers={"User-Agent": dossier.USER_AGENT}, timeout=30, follow_redirects=True
    )


def wikipedia_url(title: str) -> str:
    """The article URL if the article exists and is not a disambiguation page, else ''."""
    title = title.strip().replace(" ", "_")
    if not title:
        return ""
    try:
        response = _get(WIKI_SUMMARY + quote(title, safe=""))
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        return ""
    data = response.json()
    if data.get("type") == "disambiguation":
        return ""
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page")
    return page or f"https://en.wikipedia.org/wiki/{quote(title)}"


@dataclass
class Paper:
    title: str
    year: int | None
    citations: int
    urls: list[str]

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year}, cited {self.citations} times)"


def open_papers(query: str, concept: str) -> list[Paper]:
    """Open-access papers on OpenAlex whose title names the concept, most relevant and cited first.

    Every open-access copy OpenAlex knows (publisher PDF, repository, preprint) is kept, because a
    publisher page often cannot be read as text while a repository copy can.
    """
    try:
        response = _get(
            OPENALEX,
            {
                "search": query,
                "filter": "is_oa:true",
                "per_page": 8,
                "select": "display_name,publication_year,cited_by_count,best_oa_location,locations",
            },
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    wanted = tokens(f"{concept} {query}")
    # The paper's title must name the concept itself, not merely share common words with the query.
    named = tokens(concept) - _GENERIC
    scored = []
    for work in response.json().get("results") or []:
        title = str(work.get("display_name") or "")
        title_tokens = tokens(title)
        overlap = len(title_tokens & wanted)
        citations = int(work.get("cited_by_count") or 0)
        if overlap < 2 or (named and not title_tokens & named) or citations < MIN_CITATIONS:
            continue  # an established finding that names the concept, not a paper sharing common words
        urls: list[str] = []
        for location in [work.get("best_oa_location") or {}, *(work.get("locations") or [])]:
            if not isinstance(location, dict) or not location.get("is_oa", True):
                continue
            for key in ("pdf_url", "landing_page_url"):
                url = location.get(key) or ""
                if url.startswith("http") and url not in urls:
                    urls.append(url)
        if urls:
            scored.append(((overlap, citations), Paper(title, work.get("publication_year"), citations, urls)))
    return [paper for _score, paper in sorted(scored, key=lambda item: item[0], reverse=True)]


def open_paper(query: str, concept: str) -> tuple[str, str]:
    """(url, label) of the best matching open-access paper, first copy listed; ('', '') if none."""
    papers = open_papers(query, concept)
    return (papers[0].urls[0], papers[0].label) if papers else ("", "")


def _usable(url: str, question: str, concept: str = "") -> bool:
    """The source is fetchable, has enough text, and actually discusses the concept (named at least twice)."""
    try:
        sources = dossier.build([url], question)
    except AppError:
        return False
    if not sources:
        return False
    named = tokens(concept) - _GENERIC
    if not named:
        return True
    words = tokens_list(sources[0].text)
    return sum(1 for word in words if word in named) >= 2


def _known_titles(session: Session, brand: Brand, backlog: list[dict]) -> list[str]:
    rows = session.scalars(
        select(ContentOpportunity.title).where(ContentOpportunity.brand_id == brand.id)
    ).all()
    return [str(topic.get("title") or "") for topic in backlog] + list(rows)


def propose(session: Session, brand: Brand, known: list[str], count: int) -> list[Candidate]:
    from app.services.production.llm import cheap_json
    from app.services.strategy import STRUCTURES

    system = (
        "You plan topics for a faceless short-form channel. "
        f"Niche: {brand.niche}\nAudience: {brand.audience}\n"
        "Propose topics that a broad English-speaking audience finds surprising and relatable, that are "
        "backed by a well-known published study or a replicated finding, that are safe for advertisers "
        "(no health scares, tragedy, politics), that can be shown with simple objects, numbers and "
        "comparisons, and that explain behaviour, never advise the viewer what to buy, borrow, or invest. "
        "Prefer findings with a concrete experiment (who, what they did, what changed). Include topics "
        "where later research complicated the famous result; that disagreement is a good story.\n"
        f'Return JSON: {{"topics": [{{"title": str (under 60 chars), "question": str (the viewer\'s '
        f'question), "concept": str (the named effect or finding), "wikipedia": str (exact English '
        f'Wikipedia article title), "paper_query": str (title words of the original paper), '
        f'"structure": one of ({", ".join(STRUCTURES)}), "theme": str (2-4 words)}}]}} '
        f"with {count} topics."
    )
    user = "Already covered or planned (avoid these and close variants):\n" + "\n".join(
        f"- {title}" for title in known[:80]
    )
    result = cheap_json(session, None, operation="topic-proposals", system=system, user=user)
    out = []
    for item in (result or {}).get("topics") or []:
        if not isinstance(item, dict) or not item.get("question") or not item.get("concept"):
            continue
        out.append(
            Candidate(
                title=str(item.get("title") or item["question"])[:120],
                question=str(item["question"])[:300],
                concept=str(item["concept"])[:120],
                wikipedia=str(item.get("wikipedia") or item["concept"])[:120],
                paper_query=str(item.get("paper_query") or item["concept"])[:200],
                structure=str(item.get("structure") or "") if item.get("structure") in STRUCTURES else "",
                theme=str(item.get("theme") or "")[:60],
            )
        )
    return out


def verify(candidate: Candidate, brand: Brand, known: list[str]) -> Candidate:
    from app.services.business.signals import hold_reason

    if any(jaccard(candidate.title, other) >= 0.5 for other in known if other):
        candidate.rejected = "too close to an existing topic"
        return candidate
    reason = hold_reason(candidate.title, candidate.question, brand)
    if reason:
        candidate.rejected = f"held: {reason}"
        return candidate
    primary: Paper | None = None
    for paper in open_papers(candidate.paper_query, candidate.concept)[:2]:
        usable = next(
            (url for url in paper.urls[:4] if _usable(url, candidate.question, candidate.concept)), ""
        )
        if usable:
            candidate.sources.append(usable)
            candidate.checks.append(f"OpenAlex open-access paper fetched: {paper.label}")
            primary = paper
            break
        candidate.checks.append(f"OpenAlex paper found but no copy readable as text: {paper.label}")
    wiki = wikipedia_url(candidate.wikipedia)
    if wiki and _usable(wiki, candidate.question, candidate.concept):
        candidate.sources.append(wiki)
        candidate.checks.append("Wikipedia article exists and was fetched")
    if not candidate.sources:
        candidate.rejected = "no fetchable source"
    elif not any("wikipedia.org" in url for url in candidate.sources) and (
        primary is None or primary.citations < 100
    ):
        # A single paper with no encyclopedia confirmation must at least be a well-established one.
        candidate.rejected = "only one little-cited paper; not enough to build a video on"
    elif len(candidate.sources) == 1 and "wikipedia.org" in candidate.sources[0]:
        candidate.checks.append("Secondary source only (Wikipedia); a primary paper was not reachable")
    return candidate


def find_topics(session: Session, brand: Brand, path: Path, count: int = 8) -> dict:
    """Propose, verify, and append topics to the backlog file. Returns what was added and rejected."""
    data = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else {"brand": brand.slug, "topics": []}
    )
    backlog = data.get("topics") or []
    known = _known_titles(session, brand, backlog)
    candidates = propose(session, brand, known, count)
    added, rejected = [], []
    for candidate in candidates:
        verify(candidate, brand, known)
        if candidate.rejected:
            rejected.append({"title": candidate.title, "why": candidate.rejected})
            continue
        entry = {
            "title": candidate.title,
            "question": candidate.question,
            "structure": candidate.structure,
            "theme": candidate.theme,
            "sources": candidate.sources,
            "added_by": "topic-agent",
            "checked": utc_now().date().isoformat(),
            "checks": candidate.checks,
        }
        backlog.append(entry)
        known.append(candidate.title)
        added.append(entry)
    data["topics"] = backlog
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"added": added, "rejected": rejected}


def primary_share(entries: list[dict]) -> float:
    if not entries:
        return 0.0
    primary = sum(
        1 for entry in entries if any("wikipedia.org" not in url for url in entry.get("sources") or [])
    )
    return primary / len(entries)
