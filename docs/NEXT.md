# Next projects

## Reading rules

Use source evidence to recover the document's structure and metadata. Keep the original words and account for each region once. Freeze the development inputs before editing rules, and keep held-out documents untouched.

| Rule | Replay check |
| --- | --- |
| Establish margins, columns and tolerances from each page's geometry and text scale. Keep unclear regions. | Diff order, boxes, dispositions and region coverage. Use shifted columns, rotated pages and sparse layouts. |
| Derive heading levels from tags or bookmarks, then local typography, numbering and order. Allow no headings. | Compare heading text, level, parent and anchor in the recorded paper and heading-free form. |
| Join captions, figures and references using source links or corroborated region ownership, order and citation structure. Keep unresolved text readable. | Compare parents, spans, reference links and crop hashes. |
| Classify run-ins and front or back matter by their observed boundaries and roles. Require evidence for every promotion or omission, using genre as context. | Diff role, anchor and omission reason, including run-ins, notices, unmarked abstracts and uncaptained figures. |
| Give abstract, outline and passage eligibility the same revision's observed roles, with explicit legacy markers at the read edge. | Diff roles and passages, then reopen stored card, viewer and retrieval against pinned revisions. |
| Keep identifiers and dates with their origins. Distinguish publication, preprint and file dates, and publisher from producer. Group works only on corroborated identifiers. | Diff typed metadata and anchors. Use a multi-source fixture for grouping and conflicting IDs. |
| Keep the document title as printed. Replace an editable source title only if its provenance marks it provisional. | Diff title and anchor. Exercise capture, rename and reprocessing. |

Work in that order. Check the revision and grouping behavior in storage tests as well as the offline snapshot.

### Offline gate

The committed inputs are recorded Docling layouts for a generated paper and form, plus the synthetic paper with and without an explicit abstract marker. The four-input replay takes about 15 seconds. It exercises native PDF evidence and crop rendering with the recorded layouts, with model calls disabled. The 13-paper development gate requires the authorized `/tmp/gunther-harness/out/*/raw.json` files and matching originals. Raw recordings alone exercise layout structure, while source text and crop checks need the PDFs or equivalent evidence.

From `apps/backend`, run the fixture checks inside an isolated loopback namespace:

```sh
GUNTHER_DESKTOP_SIDECAR=1 SEMANTIC_SEARCH=false HF_HUB_OFFLINE=1 \
  .venv/bin/python -m pytest -q tests/test_pdf_fidelity.py tests/test_structure_draft.py \
  tests/test_document_pipeline.py tests/test_docling_upgrade.py
```

For before and after snapshots, use the same dependencies and fonts at each revision. From `apps/backend` set the output directory and run:

```sh
PYTHONPATH="$PWD/tests:$PWD" .venv/bin/python - /tmp/gunther-reading-before <<'PY'
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch
from gunther.document_parser import structured_document
from test_pdf_fidelity import build, parser
from test_structure_draft import two_page_paper

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

def save(name, reading):
    snapshot = {
        "manifest": reading.manifest,
        "elements": [asdict(element) for element in reading.elements],
        "passages": [asdict(passage) for passage in reading.chunks()],
        "assets": [{"key": asset.key, "sha256": sha256(asset.data).hexdigest()}
                   for asset in reading.assets],
    }
    (out / f"{name}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

with TemporaryDirectory() as temporary:
    for name in ("paper", "form"):
        case = Path(temporary) / name
        case.mkdir()
        with MonkeyPatch.context() as patches:
            save(name, parser(case).parse(build(case, patches, name), reviser=None))

for marked in (True, False):
    doc, _ = two_page_paper(marked_abstract=marked)
    save(f"abstract-{'marked' if marked else 'unmarked'}",
         structured_document(doc.raw, {}, reviser=None))
PY
```

Repeat at the candidate revision with `/tmp/gunther-reading-after` and compare using `diff -ru /tmp/gunther-reading-before /tmp/gunther-reading-after`. Review changed regions by source, page and span or bounding box. Confirm zero unaccounted blocks and check each omission's reason. [Recorded-layout replay](../apps/backend/tests/pdf_replay.py) · [Published reading tests](../apps/backend/tests/test_pdf_fidelity.py)

### Studio claims and covers

Share one cover model between the renderer and Checker. Give it fields for the title, full subtitle and citations, and metadata with provenance. Read saved covers through this model and replace only metadata on normalization.

Give numerical claims an identity that includes field role, referent, effective unit and evidence. Resolve the unit before rendering and checking, carry it through table fallback, and reuse a verdict only when that identity still matches. Check numeric table cells individually. Run the offline Studio and Checker fixtures for these cases:

| Case | Expected behavior |
| --- | --- |
| The cover date is `Spring 2026`. | Display it as metadata and exclude it from claims. |
| The subtitle continues with `because the review began in 2024`. | Check the full dated claim. |
| The first column contains a numeric claim. | Check it according to its declared role. |
| Chart values change from `ms` to `s`, then fall back and retry. | Obtain fresh verdicts and keep the effective unit in fallback and retry. |
| One numeric table cell cites a source and its neighbor has none. | Flag the uncited cell. |
| A journal-club question wraps onto an unindented, uncited line. | Preserve the entire subtitle. |
| A continuation in the subtitle carries `[i:1]`. | Keep it in both rendering and checking with the citation. |
| Source metadata is applied twice to a metadata-only cover. | Apply it idempotently. |

[Cover parser](../apps/desktop/src/outputs/deck/parseDeck.ts) · [Source cover](../apps/backend/gunther/studio/science.py) · [Studio verification](../apps/backend/gunther/studio/verification.py)

## Recorder state machine

Specify one transition model for live capture, staged import, pause, finish, review, save and recovery. Persist the origin and staging completeness so a damaged import cannot reopen as a live session after its last acknowledged chunk is removed. Reconcile the server ledger and local spool against the manifest before sending a suffix or cleaning any files. Keep staged and acknowledged audio recoverable across a lost reply, Stop, restart or storage error.

Store the client's edit time with each transcript snapshot on both sides. Compare snapshot edit times in recovery. The current server `checkpointedAt` records commit time, so a delayed reply can make an older edit look later. Cover delayed checkpoints, stopped-phase autosave races, trailing newlines and reopening after spool cleanup. Local text must survive recovery and a failed checkpoint. [Recorder](../apps/desktop/src/components/LectureRecorder.tsx) · [Checkpoint](../apps/backend/gunther/recording_service.py) · [Recovery tests](../apps/desktop/src/components/LectureRecorder.saving.test.tsx)

## Revision-scoped abstracts

Give each immutable reading revision its own paper-facts record, including the abstract and its block IDs. Keep generated summaries and model-specific embeddings alongside that record. Publish under the worker lease and current-head guard. Query vectors by eligible revision and read the matching record for current or pinned use. Seed legacy cards where their blocks belong to their recorded revision, and rebuild from the revision's blocks when needed.

Exercise retries that produce different abstract boundaries, a changed embedder and a head that leaves and returns to a published revision. Verify prior abstracts, block links and citations still open throughout. [Paper publication](../apps/backend/gunther/papers.py) · [Ranked read](../apps/backend/gunther/knowledge_index.py) · [Worker](../apps/backend/gunther/processing.py)
