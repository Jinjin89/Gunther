"""Search provenance and live library matches for web evidence."""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from gunther.evidence import Evidence, EvidenceKey, KeyResultRefs, Tool
from gunther.models import KnowledgeBaseSource, Source, SourcePaper, WebSnapshot
from gunther.online_search import SNIPPET_LIMIT
from gunther.papers import ARXIV, DOI, _clean_doi
from gunther.schemas import ConversationCitationOut, SearchedHitOut, UsedKeyResultOut
from gunther.service_records import HOME_SCOPE

MAX_SEARCHED = 20
MAX_SEARCHED_WEB = 12


def _canonical_url(url: str) -> str | None:
    # Web capture depends on the service that uses this lookup.
    from gunther.web_capture import WebCaptureValidationError, normalize_web_url

    try:
        return normalize_web_url(url).url
    except WebCaptureValidationError:
        return None


def _identifiers(values: tuple[str | None, ...]) -> tuple[str, ...]:
    urls, dois, arxiv_ids = [], [], []
    for position, value in enumerate(values):
        if not value:
            continue
        if url := _canonical_url(value):
            urls.append(url)
            path = unquote(urlsplit(url).path).strip("/")
            doi = DOI.search(path)
            route, _, identifier = path.casefold().partition("/")
            if route == "pdf":
                identifier = identifier.removesuffix(".pdf")
            arxiv = ARXIV.fullmatch(f"arXiv:{identifier}") if route in {"abs", "pdf"} else None
        elif position > 0:
            value = value.strip().casefold()
            doi = DOI.fullmatch(value.removeprefix("doi:"))
            arxiv = ARXIV.fullmatch(f"arXiv:{value.removeprefix('arxiv:')}")
        else:
            continue
        if doi:
            dois.append(f"doi:{_clean_doi(doi[1])}")
        if arxiv:
            arxiv_ids.append(f"arxiv:{arxiv[1]}")
    return (*urls, *dois, *arxiv_ids)


def resolve_library_sources(
    session: Session, base_id: str, item_identifiers: Iterable[tuple[str | None, ...]],
) -> dict[tuple[str | None, ...], str]:
    """Batch live matches for items given as a URL followed by explicit identifiers."""
    if base_id == HOME_SCOPE:
        return {}
    requests = {values: _identifiers(values) for values in dict.fromkeys(item_identifiers)}
    wanted = {key for keys in requests.values() for key in keys}
    if not wanted:
        return {}
    rows = session.execute(
        select(Source.id, WebSnapshot.original_url, WebSnapshot.final_url,
               SourcePaper.doi, SourcePaper.arxiv_id)
        .select_from(KnowledgeBaseSource)
        .join(Source, Source.id == KnowledgeBaseSource.source_id)
        .outerjoin(WebSnapshot, WebSnapshot.source_id == Source.id)
        .outerjoin(SourcePaper, SourcePaper.source_id == Source.id)
        .where(
            KnowledgeBaseSource.knowledge_base_id == base_id,
            Source.trashed_at.is_(None),
            or_(WebSnapshot.source_id.is_not(None), SourcePaper.source_id.is_not(None)),
        )
        .order_by(KnowledgeBaseSource.created_at, Source.id)
    )
    matches: dict[str, str] = {}
    for source_id, original, final, doi, arxiv_id in rows:
        for key in (
            _canonical_url(original) if original else None,
            _canonical_url(final) if final else None,
            f"doi:{_clean_doi(doi)}" if doi else None,
            f"arxiv:{arxiv_id}" if arxiv_id else None,
        ):
            if key is not None and key in wanted:
                matches.setdefault(key, source_id)
    return {
        values: source_id
        for values, keys in requests.items()
        if (source_id := next((matches[key] for key in keys if key in matches), None)) is not None
    }


def annotate_library_sources(
    session: Session, base_id: str, items: Iterable[ConversationCitationOut | SearchedHitOut],
) -> None:
    """Enrich deserialized evidence for the API without changing its stored payload."""
    web = []
    for item in items:
        if item.kind != "web":
            continue
        fields: list[object] = []
        if isinstance(item, ConversationCitationOut):
            fields.extend((
                item.work_identifier, item.source_metadata.get("doi"),
                item.source_metadata.get("arxiv_id"), item.source_metadata.get("arxivId"),
            ))
        values = (item.url, *(field for field in fields if isinstance(field, str) and field))
        web.append((item, values))
    matches = resolve_library_sources(session, base_id, (values for _, values in web))
    for item, values in web:
        item.library_source_id = matches.get(values)


@dataclass(frozen=True)
class SearchHit:
    item: Evidence
    query: str
    # When the search has no grading decision, use membership in the run's pool.
    kept: bool | None = None


def track_search(tool: Tool, searched: list[SearchHit]) -> Tool:
    """Record the tool's discoveries without changing what the caller sees."""

    def run(query: str) -> list[Evidence]:
        found = tool.run(query)
        searched.extend(SearchHit(item, query) for item in found)
        return found

    return replace(tool, run=run)


def _hit_key(item: Evidence) -> EvidenceKey:
    if item.kind == "web" and item.url:
        return (item.kind, _canonical_url(item.url) or item.url, "")
    source = getattr(item.citation, "source_id", None)
    block = getattr(item.citation, "block_id", None)
    return (item.kind, source, block) if source and block else item.key


def pack_searched(
    hits: Iterable[SearchHit], cited: Iterable[Evidence], kept_keys: Collection[EvidenceKey] = (),
) -> list[SearchedHitOut]:
    """Keep the first 12 uncited web hits, then fill the 20 slots from the library."""
    from gunther.web_capture import MAX_WEB_URL_LENGTH

    used = {_hit_key(item) for item in cited}
    distinct: dict[EvidenceKey, tuple[SearchHit, bool]] = {}
    for hit in hits:
        key = _hit_key(hit.item)
        if key in used:
            continue
        kept = hit.kept if hit.kept is not None else hit.item.key in kept_keys
        previous = distinct.get(key)
        # A lookup's query and decision take precedence over a whole-source reading.
        if previous is None or (bool(hit.query), kept) > (bool(previous[0].query), previous[1]):
            distinct[key] = (hit, kept)
    web = [pair for pair in distinct.values() if pair[0].item.kind == "web"][:MAX_SEARCHED_WEB]
    library = [pair for pair in distinct.values() if pair[0].item.kind == "library"]
    packed = []
    for hit, kept in [*web, *library[:MAX_SEARCHED - len(web)]]:
        item = hit.item
        url = item.url
        # Keep the same text bounds as web search and Studio passage locators.
        packed.append(
            SearchedHitOut(
                kind="web" if item.kind == "web" else "library", title=item.title[:200],
                url=url if url and len(url) <= MAX_WEB_URL_LENGTH else None,
                source_id=getattr(item.citation, "source_id", None) or None,
                locator=item.locator[:240], snippet=item.text[:SNIPPET_LIMIT],
                query=hit.query[:400], kept=kept,
            )
        )
    return packed


def pack_key_results(results: KeyResultRefs, cited: Iterable[Evidence]) -> list[UsedKeyResultOut]:
    reading = {item.key: number for number, item in enumerate(cited, 1)}
    return [
        UsedKeyResultOut(
            id=result_id, text=text, refs=sorted(reading[key] for key in refs if key in reading),
        )
        for result_id, (text, refs) in results.items()
    ]
