# Development principles

## General rules, never special cases

The most important rule. When something fails for one document, one model, one
paper or one question, find the general rule that failed and fix that rule. Never
add a branch for the case you happened to see.

- Not allowed in production code: names of particular documents, papers, journals,
  publishers, websites, people, example questions, sample libraries, test fixtures,
  or keyword/regex lists tuned until one example passes. Thresholds and limits must
  come from a stated reason, not from making one fixture pass.
  - Bad: `if "Nature" in publisher: skip_first_page()`
  - Good: detect a cover page from layout (no body text, large image, page 1) and
    skip any cover page.
- If a case really is different, it is data (a named entry in a table or config
  with a one-line reason), and the general path still handles everything else.
- Regression examples verify general behavior. They live in tests, never in the
  implementation.
- When you notice an existing special case, remove it or report it. Don't build on it.

## Abstraction and simplicity

Both come first.

- Each job has one path. When a new way of doing something replaces an old one,
  delete the old one in the same change. Don't keep it behind a setting, a
  developer switch or a fallback "just in case". Old saved data still has to open:
  keep a small read-only converter, not the old pipeline.
- Build features from a few shared pieces (model call that returns checked JSON,
  step runner with progress and resume, source reader, settings store, HTTP
  helpers, UI form fields). If the same thing is written three times, make it one.
  Don't add layers, frameworks or "manager" classes that have only one user.
- Delete dead code, unused exports, orphan screens and experiment flags once
  the experiment is over. Git keeps the history.
- Prefer a well-known library to hand-rolled code.
- Dev tools (evals, benchmarks, debug viewers) are repo commands, not app UI.

## Documents and reading

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

## Docs

- Docs describe the system as it is now. When a plan is built, fold what is still
  true into the guide and delete the plan. No progress logs, no "uncommitted"
  notes, no review instructions for work that has shipped.
