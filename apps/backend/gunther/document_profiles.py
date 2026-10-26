"""What each kind of document usually holds, and what counts as junk in it.

The model that corrects a document's structure (see structure_model) reads every
guide and follows the one for the type it decides on. A type changes what is
expected and what is dropped, never the shape of the plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from gunther.document_model import DOCUMENT_TYPES


@dataclass(frozen=True)
class Profile:
    type: str
    # What it is, so the model can recognise it.
    what: str
    front: str
    body: str
    back: str
    # What to drop, besides the page furniture every type drops.
    junk: str
    # Anything else that is special to this type.
    also: str = ""

    def guide(self) -> str:
        lines = [
            f"{self.type}: {self.what}",
            f"  Front: {self.front}",
            f"  Body: {self.body}",
            f"  Back: {self.back}",
            f"  Drop: {self.junk}",
        ]
        if self.also:
            lines.append(f"  Also: {self.also}")
        return "\n".join(lines)


PROFILES: dict[str, Profile] = {
    profile.type: profile
    for profile in (
        Profile(
            "paper",
            "A research paper, preprint or journal article.",
            front=(
                "the title, the authors' names, the abstract (even when it is printed without "
                "a heading) and the keywords."
            ),
            body=(
                "sections such as Introduction, Methods, Results and Discussion, with their "
                "figures, tables and formulas."
            ),
            back=(
                "the reference list (part references), appendices (part appendix), and the "
                "data availability and code availability statements (part availability)."
            ),
            junk=(
                "affiliations and addresses, e-mail addresses, correspondence lines, "
                "received/accepted/published lines, competing interests, author contributions, "
                "funding, acknowledgements, publisher notes, licence and copyright lines, "
                "reprint and peer-review notices, journal logos and 'check for updates' badges."
            ),
            also="A picture with no caption is decoration (a logo, an icon): drop it.",
        ),
        Profile(
            "book",
            "A book, thesis or other long text divided into chapters.",
            front=(
                "the title, and a preface or foreword (part preface). A dedication or "
                "acknowledgements go in a front part other."
            ),
            body="chapters as level 1 sections, with their sections and subsections below them.",
            back=(
                "the bibliography (part references), appendices (part appendix) and a glossary "
                "(part glossary)."
            ),
            junk=(
                "the printed table of contents and the index (the reader builds its own "
                "contents)."
            ),
            also=(
                "Footnotes and endnotes are content: define them as notes, with their marker, "
                "and keep them."
            ),
        ),
        Profile(
            "report",
            "A report, white paper, brief or proposal, often with a summary at the start.",
            front=(
                "the title, then the organisation and the date (a front part other), then the "
                "summary (part summary)."
            ),
            body=(
                "sections under their headings. Boxes and call-outs are content: keep them in "
                "the section where they sit."
            ),
            back="appendices (part appendix) and references (part references), if there are any.",
            junk="nothing besides page furniture.",
        ),
        Profile(
            "slides",
            "A slide deck saved as a PDF: one slide per page, with short text and pictures.",
            front="the title slide: the deck's title, subtitle and presenter.",
            body=(
                "one level 1 section per slide, with the slide's title as the heading and its "
                "text, lists, pictures and tables in the section."
            ),
            back="nothing; a closing slide is just the last section.",
            junk="slide numbers, and footers, dates and logos that repeat on every slide.",
            also=(
                "Pictures are content even without a caption: make each one a figure, with an "
                "empty caption."
            ),
        ),
        Profile(
            "article",
            "A web article, blog post or news story saved as a PDF.",
            front="the title, the byline (authors) and the date (a front part other).",
            body="paragraphs and images under the article's own headings.",
            back="nothing.",
            junk=(
                "navigation menus, related-article links, share and follow buttons, cookie and "
                "subscribe banners, advertisements, comments and the site footer."
            ),
            also=(
                "Images that illustrate the article are figures (a caption is optional); icons "
                "and logos are decoration: drop them."
            ),
        ),
        Profile(
            "manual",
            "A manual, standard, specification, regulation or contract made of numbered clauses.",
            front="the title, and the version, edition or date (a front part other).",
            body=(
                "the numbered clauses. The numbering is the structure: 3.2.1 is a level 3 "
                "heading, inside 3.2 and 3. A numbered clause with no title of its own is a "
                "paragraph, not a heading."
            ),
            back="annexes and appendices (part appendix), and a glossary (part glossary).",
            junk="only page furniture, such as the document number repeated on every page.",
        ),
        Profile(
            "unknown",
            "A document that fits none of the other types.",
            front="the title, if it has one.",
            body="the headings as printed, with their levels, and the text under them.",
            back="nothing special.",
            junk="only page furniture.",
            also="Expect nothing: do not invent a title, an abstract or references.",
        ),
    )
}


def guides() -> str:
    """Every type's guide, in the order of ``DOCUMENT_TYPES``."""

    return "\n\n".join(PROFILES[name].guide() for name in DOCUMENT_TYPES)
