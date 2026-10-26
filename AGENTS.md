# Development principles

- Build general capabilities. Do not encode individual documents, publishers,
  questions, or examples as production rules.
- Recover each document's own organization before preparing retrieval passages.
  Prefer explicit structure, bookmarks, reading order, typography and layout.
  Documents can have different sections, or no headings at all; do not impose
  a paper template or manufacture section names.
- Keep the original file and provenance. The reading version uses the
  document's own text, never rewritten: every block is placed once (metadata,
  body text, visual content with its caption, references) or dropped with a
  recorded reason (page headers/footers, decoration, and back matter the
  document type does not read as content, such as competing interests).
  Report incomplete extraction rather than guessing.
- Distinguish publication dates from file creation/modification dates, and
  publishers from the software that produced a file.
- Regression examples verify general behavior; they must not become special
  cases in the implementation.
