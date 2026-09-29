"""Papers: what each source says about itself, and which sources are copies.

Everything here is read from the source's current revision without a model:

- the title: the largest type on a PDF's first page, else the PDF's metadata,
  else the source's own title;
- authors: PDF metadata, or the names printed under the title;
- the year: an arXiv id, a dated notice on the first page, or the PDF's date;
- a DOI or arXiv id;
- the abstract, with the blocks it came from, so it can be cited;
- the outline: the section headings.

The result is kept in ``source_papers`` and written as ``summary.md`` in the
library folder. Copies of one paper (the same DOI or arXiv id, else the same
title) share a row in ``works``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from gunther import vector_index
from gunther.knowledge_index import new_id
from gunther.models import ContentBlock, Source, SourcePaper, Work, utc_now
from gunther.pdf_text import looks_like_heading, title_by_font

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
ARXIV = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
YEAR_NOTICE = re.compile(
    r"(?:©|\(c\)|copyright|published|accepted|received|proceedings|conference|journal|"
    r"volume|vol\.)\D{0,40}?\b((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
ABSTRACT_NAMES = frozenset({"abstract", "summary", "摘要"})
ABSTRACT_LEAD = re.compile(r"^(?:abstract|摘要)\s*[.:：—–-]\s*|^(?:abstract|摘要)\s+", re.I)
ABSTRACT_INLINE = re.compile(r"\s(?:Abstract|ABSTRACT|摘要)\s*[.:：—–]\s+")
# Where an abstract ends, besides the next heading: footnotes and venue notices.
ABSTRACT_END = re.compile(
    r"^(?:[∗*†‡§¶]|\d+(?:st|nd|rd|th)\s+conference|©|copyright|preprint|keywords\b|关键词)",
    re.IGNORECASE,
)
NOT_A_TITLE = re.compile(
    r"\.(?:pdf|docx?|tex|dvi|indd)$|^microsoft word|^untitled|^arxiv:|^slide \d", re.IGNORECASE
)
EMAIL = re.compile(r"[^\s(),;]*@[^\s(),;]+")
MARKERS = re.compile(r"[∗*†‡§¶#\d]+")
NAME_WORD = re.compile(r"(?:[^\W\d_]|[˘´`¨ˆ˜'’.\-])+")
PARTICLES = frozenset({"van", "von", "de", "der", "den", "da", "di", "du", "le", "la", "del"})
ORGANIZATION = re.compile(
    r"\b(?:universit\w*|univ|institut\w*|college|school|faculty|department|dept|"
    r"laborator\w*|labs?|research|corporation|corp|inc|llc|ltd|gmbh|google|microsoft|"
    r"facebook|meta|openai|deepmind|nvidia|amazon|apple|ibm|baidu|alibaba|tencent|"
    r"bytedance|huawei|brain|ai|academy|hospital|cent(?:er|re)|team|group|foundation)\b",
    re.IGNORECASE,
)
MAX_ABSTRACT = 2400
GIST = 800
# Titles shorter than this are too generic ("Introduction") to match copies by.
MIN_TITLE_KEY = 20


@dataclass(frozen=True, slots=True)
class PdfFacts:
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    doi: str | None = None


@dataclass(slots=True)
class PaperFacts:
    title: str
    authors: str = ""
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str = ""
    abstract_block_ids: list[str] = field(default_factory=list)
    outline: list[str] = field(default_factory=list)
    # The title came from the document itself, not the name it was saved under.
    title_from_document: bool = False

    @property
    def identifier(self) -> str | None:
        if self.doi:
            return f"doi:{self.doi}"
        return f"arxiv:{self.arxiv_id}" if self.arxiv_id else None


def _plausible_title(value: str | None) -> str | None:
    if not value:
        return None
    title = re.sub(r"\s+", " ", value).strip()
    words = len(title.split())
    cjk = len(re.findall(r"[㐀-鿿]", title))
    if not 8 <= len(title) <= 300 or (words < 2 and cjk < 4) or NOT_A_TITLE.search(title):
        return None
    return title


def _clean_doi(value: str) -> str:
    return value.rstrip(".,;:)]}>").casefold()


def pdf_facts(path: Path) -> PdfFacts:
    """Title, authors, year and DOI a PDF declares about itself, when it does."""

    from pypdf import PdfReader

    try:
        reader = PdfReader(path, strict=False)
        meta = reader.metadata or {}
    except Exception:
        return PdfFacts()
    title = _plausible_title(title_by_font(reader)) or _plausible_title(
        str(meta.get("/Title") or "")
    )
    # Metadata sometimes carries emails: "Olaf Ronneberger (ronneber@…), …".
    author = re.sub(r"\(\s*\)", "", EMAIL.sub("", str(meta.get("/Author") or "")))
    author = re.sub(r"\s+([,;])", r"\1", re.sub(r"\s+", " ", author)).strip(" ,;")
    if author.casefold() in {"", "unknown", "administrator", "user", "author"}:
        author = ""
    created = re.match(r"D:((?:19|20)\d{2})", str(meta.get("/CreationDate") or ""))
    doi = next(
        (DOI.search(str(meta.get(key) or "")) for key in ("/doi", "/prism:doi", "/Subject")),
        None,
    )
    return PdfFacts(
        title=title,
        authors=author[:500] or None,
        year=int(created[1]) if created else None,
        doi=_clean_doi(doi[1]) if doi else None,
    )


def _page(block: ContentBlock) -> int | None:
    page = json.loads(block.anchor_json or "{}").get("page")
    return page if isinstance(page, int) else None


def _alnum(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _name(part: str) -> str | None:
    """A person's name, or None when the text is an affiliation, email or noise."""

    part = MARKERS.sub("", EMAIL.sub("", part)).strip(" ,;:()[]{}")
    words = part.split()
    if not 2 <= len(words) <= 4 or ORGANIZATION.search(part):
        return None
    if not all(
        NAME_WORD.fullmatch(word) and (word[0].isupper() or word in PARTICLES) for word in words
    ):
        return None
    return " ".join(words)


def _names_in(part: str) -> list[str]:
    """Names in one comma- or "and"-separated piece of an author block."""

    part = MARKERS.sub("", EMAIL.sub("", part)).strip(" ,;:()[]{}")
    organization = ORGANIZATION.search(part)
    if organization:  # "Furu Wei Microsoft Corporation": names come first
        part = part[: organization.start()]
    words = part.split()
    if len(words) >= 4 and len(words) % 2 == 0 and all(w[:1].isupper() for w in words):
        # "Kaiming He Xiangyu Zhang Shaoqing Ren": names printed side by side.
        pairs = [" ".join(words[i : i + 2]) for i in range(0, len(words), 2)]
        return [name for pair in pairs if (name := _name(pair))]
    if len(words) > 3 and organization:
        words = words[:2]
    name = _name(" ".join(words))
    return [name] if name else []


def _authors(front: Sequence[ContentBlock], title: str) -> str:
    """The names printed between the title and the abstract."""

    text = "\n".join(block.content for block in front[:25])
    wanted = _alnum(title)
    if len(wanted) < 8:
        return ""
    # Find where the title ends in the page text, whatever its spacing or case.
    seen, end = "", None
    for index, character in enumerate(text):
        if character.isalnum():
            seen += character.casefold()
            if seen.endswith(wanted):
                end = index + 1
                break
    if end is None:
        return ""
    region = text[end:]
    stop = re.search(r"^\s*(?:abstract|摘要)\b", region, re.IGNORECASE | re.MULTILINE)
    region = region[: stop.start() if stop else 1500]
    names: list[str] = []
    for part in re.split(r"\n|,|;|\band\b|&", region):
        for name in _names_in(part):
            if name not in names:
                names.append(name)
    return ", ".join(names[:30])


def _abstract(blocks: Sequence[ContentBlock]) -> tuple[str, list[str]]:
    start = None
    parts: list[tuple[str, str]] = []
    for index, block in enumerate(blocks):
        text = block.content.strip()
        if text.casefold().rstrip(" .:：") in ABSTRACT_NAMES:
            start = index + 1
            break
        lead = ABSTRACT_LEAD.match(text)
        if lead and block.kind != "heading" and len(text) - lead.end() >= 60:
            parts.append((block.id, text[lead.end():]))
            start = index + 1
            break
        # "…University of Freiburg, Germany Abstract. There is large consent…"
        inline = ABSTRACT_INLINE.search(text) if index < 40 else None
        if inline and block.kind != "heading" and len(text) - inline.end() >= 60:
            parts.append((block.id, text[inline.end():]))
            start = index + 1
            break
    if start is None:
        return "", []
    for block in blocks[start:]:
        text = block.content.strip()
        if (
            block.kind == "heading"
            or looks_like_heading(text)
            or ABSTRACT_END.match(text)
            or sum(len(part) for _, part in parts) >= MAX_ABSTRACT
        ):
            break
        parts.append((block.id, text))
    return " ".join(part for _, part in parts)[:MAX_ABSTRACT], [block for block, _ in parts]


def _gist(blocks: Sequence[ContentBlock]) -> tuple[str, list[str]]:
    """The opening of a source without an abstract: its first real paragraphs."""

    parts: list[tuple[str, str]] = []
    for block in blocks:
        text = block.content.strip()
        if block.kind in {"heading", "table", "figure"} or looks_like_heading(text):
            continue
        if len(text) < 60 and not parts:
            continue  # front matter: names, affiliations, dates
        parts.append((block.id, text))
        if sum(len(part) for _, part in parts) >= GIST:
            break
    return " ".join(part for _, part in parts)[:GIST * 2], [block for block, _ in parts]


def _outline(blocks: Sequence[ContentBlock]) -> list[str]:
    seen: list[str] = []
    for block in blocks:
        text = re.sub(r"\s+", " ", block.content).strip()
        if block.kind != "heading" and not looks_like_heading(text):
            continue
        if text.casefold().rstrip(" .:") in ABSTRACT_NAMES | {"references", "bibliography"}:
            continue
        if text not in seen and len(text) <= 120:
            seen.append(text)
        if len(seen) >= 40:
            break
    return seen


def read_paper(
    source_title: str, blocks: Sequence[ContentBlock], pdf: PdfFacts | None = None
) -> PaperFacts:
    """What the source says about itself. ``blocks`` in reading order."""

    pdf = pdf or PdfFacts()
    front = [block for block in blocks if (_page(block) or 1) <= 2][:80]
    front_text = "\n".join(block.content for block in front)
    title = pdf.title
    facts = PaperFacts(title=title or source_title, title_from_document=bool(title))
    doi = DOI.search(front_text)
    facts.doi = pdf.doi or (_clean_doi(doi[1]) if doi else None)
    arxiv = ARXIV.search(front_text)
    facts.arxiv_id = arxiv[1] if arxiv else None
    this_year = datetime.now(UTC).year
    if facts.arxiv_id and 7 <= int(facts.arxiv_id[:2]) <= this_year - 2000:
        facts.year = 2000 + int(facts.arxiv_id[:2])
    else:
        notice = YEAR_NOTICE.search(front_text)
        year = int(notice[1]) if notice else pdf.year
        facts.year = year if year and 1900 < year <= this_year + 1 else None
    facts.authors = pdf.authors or (_authors(front, title) if title else "")
    facts.abstract, facts.abstract_block_ids = _abstract(blocks)
    if not facts.abstract:
        facts.abstract, facts.abstract_block_ids = _gist(blocks)
    facts.outline = _outline(blocks)
    return facts


def summary_markdown(facts: PaperFacts, source_title: str) -> str:
    lines = [f"# {facts.title}", ""]
    byline = [
        part
        for part in (
            facts.authors,
            str(facts.year) if facts.year else "",
            f"DOI [{facts.doi}](https://doi.org/{facts.doi})" if facts.doi else "",
            f"arXiv [{facts.arxiv_id}](https://arxiv.org/abs/{facts.arxiv_id})"
            if facts.arxiv_id
            else "",
        )
        if part
    ]
    if byline:
        lines += [" · ".join(byline), ""]
    if facts.abstract:
        lines += ["## Abstract", "", facts.abstract, ""]
    if facts.outline:
        lines += ["## Outline", "", *[f"- {heading}" for heading in facts.outline], ""]
    lines += [
        "---",
        "",
        f"Read from “{source_title}” by Gunther, without a model: the abstract and "
        "headings are the paper's own words.",
    ]
    return "\n".join(lines) + "\n"


def title_key(title: str) -> str:
    return re.sub(r"[\W_]+", " ", title.casefold()).strip()[:300]


def _assign_work(session: Session, facts: PaperFacts) -> str:
    identifier = facts.identifier
    key = title_key(facts.title)
    work = (
        session.scalar(select(Work).where(Work.identifier == identifier)) if identifier else None
    )
    if work is None and facts.title_from_document and len(key) >= MIN_TITLE_KEY:
        work = session.scalar(
            select(Work).where(Work.title_key == key).order_by(Work.created_at).limit(1)
        )
    if work is None:
        work = Work(id=new_id("work"), identifier=identifier, title_key=key, title=facts.title)
        session.add(work)
    elif identifier and work.identifier is None:
        work.identifier = identifier
    session.flush()
    return work.id


def drop_orphan_works(session: Session) -> None:
    session.execute(
        delete(Work).where(
            Work.id.not_in(
                select(SourcePaper.work_id).where(SourcePaper.work_id.is_not(None))
            )
        )
    )


def _named_after_its_file(source: Source, file_name: str | None) -> bool:
    if not file_name:
        return False
    return source.title.strip() in {file_name, Path(file_name).stem}


def publish(
    session: Session,
    source: Source,
    revision_id: str,
    facts: PaperFacts,
    *,
    file_name: str | None = None,
    model: str | None = None,
    vector: list[float] | None = None,
) -> SourcePaper:
    """Keep what was read, group copies, and store the paper's vector."""

    if facts.title_from_document and _named_after_its_file(source, file_name):
        source.title = facts.title[:160]
    row = session.get(SourcePaper, source.id)
    if row is None:
        row = SourcePaper(source_id=source.id, revision_id=revision_id, title=facts.title)
        session.add(row)
    row.revision_id = revision_id
    row.title = facts.title[:300]
    row.authors = facts.authors
    row.year = facts.year
    row.doi = facts.doi
    row.arxiv_id = facts.arxiv_id
    row.abstract = facts.abstract
    row.abstract_block_ids_json = json.dumps(facts.abstract_block_ids)
    row.outline_json = json.dumps(facts.outline, ensure_ascii=False)
    row.summary_md = summary_markdown(facts, source.title)
    row.updated_at = utc_now()
    row.work_id = _assign_work(session, facts)
    if vector is not None and model:
        vector_index.store_paper(session, source_id=source.id, model=model, vector=vector)
        row.vector_model = model
    session.flush()
    drop_orphan_works(session)
    return row


def paper_text(facts: PaperFacts) -> str:
    """What a paper's vector is made from."""

    return f"{facts.title}\n{facts.abstract}".strip()


def copies(session: Session, source_id: str) -> list[Source]:
    """Other live sources that are copies of the same work."""

    row = session.get(SourcePaper, source_id)
    if row is None or row.work_id is None:
        return []
    return list(
        session.scalars(
            select(Source)
            .join(SourcePaper, SourcePaper.source_id == Source.id)
            .where(
                SourcePaper.work_id == row.work_id,
                Source.id != source_id,
                Source.trashed_at.is_(None),
            )
            .order_by(Source.created_at)
        )
    )
