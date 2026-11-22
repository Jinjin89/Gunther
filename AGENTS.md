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

## Rules learned in the cleanup

- Identify a thing by its stable ID or source position. A table row belongs to its exhibit index and row index.
- Get intent from explicit controls or structured model fields. Do not use word lists or regexes over free-form user or document text. Parsers for defined formats are fine.
- Keep teaching examples in model prompts.
- Let the format's decoder decide whether it can read a file. Preserve the parts it can read and report skipped parts and reasons.
- Recover each document's own structure from tags, bookmarks, reading order, typography and geometry. Allow a reading with no headings.
- Keep originals and provenance. The reading keeps the source's wording. Place each block of source text once or record its omission. Distinguish publication dates from file dates, and publishers from authoring software.
- Verify identity, eligibility and integrity before use. Filter eligible rows before a limited query.
- Derive status from the final state. Count slides still failing after the last repair.
- Give each write one owner. Stage changes, then publish together so a failed save leaves stored state intact.
- When review repeatedly finds edge cases in a heuristic, remove it or give it one owner with one precedence rule.
- Let an interrupted turn from an older build reopen with its stored work and restart from there.
- Document the system as shipped. Fold completed plans into the living guide. [Documentation index](README.md#guides)
