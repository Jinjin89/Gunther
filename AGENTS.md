# Development principles

- Build general capabilities. Do not encode individual documents, publishers,
  questions, or examples as production rules.
- Recover each document's own organization before preparing retrieval passages.
  Prefer explicit structure, bookmarks, reading order, typography and layout.
  Documents can have different sections, or no headings at all; do not impose
  a paper template or manufacture section names.
- Keep original content and provenance. Separate document metadata, page
  headers/footers, body text, visual content, captions and references. Preserve
  ambiguous information and report incomplete extraction rather than guessing.
- Distinguish publication dates from file creation/modification dates, and
  publishers from the software that produced a file.
- Regression examples verify general behavior; they must not become special
  cases in the implementation.
