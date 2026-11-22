# E0 — Phase E offline reading feasibility

**The committed fixtures can be replayed and diffed offline without model calls.** The documented 13-paper development corpus is unavailable here, so its coverage and runtime are unmeasured. Both timed runs used `d50d4e8`. The inputs were committed synthetic fixtures and their generated PDFs. [Phase E gate](/root/git/Gunther/docs/CLEANUP_PLAN.md:374) · [Documented harness paths](/root/git/Gunther/docs/DOCUMENT_STRUCTURE_PLAN.md:249)

## Corpus and measured runs

| Input or check | Evidence from this run | Wall time |
| --- | --- | --- |
| Recorded Docling paper and form | `paper.json` pairs with a generated five-page paper and `form.json` with a generated one-page form. The replay supplies recorded layout in place of the Docling subprocess. The real parser still reads native PDF evidence, renders crops, drafts structure and assembles the reading. The paper accounted for 41 evidence blocks, including 11 omissions with reasons. The form accounted for all seven blocks. [Replay](/root/git/Gunther/apps/backend/tests/pdf_replay.py:24) · [Paper recording](/root/git/Gunther/apps/backend/tests/layouts/paper.json) · [Form recording](/root/git/Gunther/apps/backend/tests/layouts/form.json) | Included in the snapshot timings below |
| Synthetic structure controls | `two_page_paper()` with and without an explicit abstract marker. Both went through `structured_document(..., reviser=None)`. The diff showed the same source region change from an abstract to ordinary body text when the marker was removed. The opening text was retained. [Structure fixture](/root/git/Gunther/apps/backend/tests/test_structure_draft.py:128) · [Control diff](/tmp/gunther-cleanup/batches/E0-logs/abstract-control.diff) | Included in the snapshot timings below |
| Four-input reading snapshot | Exported ordered canonical elements and passages, roles, anchors, metadata with provenance, omission reasons, and crop hashes. All four reported zero unaccounted blocks. The two same-commit exports matched byte for byte. [First snapshot](/tmp/gunther-cleanup/batches/E0-logs/replay-a.log) · [Second snapshot](/tmp/gunther-cleanup/batches/E0-logs/replay-b.log) · [Comparison](/tmp/gunther-cleanup/batches/E0-logs/identical-replay.diff) | **13.05 s** and **14.53 s** |
| Reader regression suites | **56 passed, 0 failed, 0 skipped**. The fixtures cover structure, the parse and upgrade boundaries, and a published reading checked against the document API, paper card and stored retrieval passages. Optional model review is exercised with a test stub. [Published-reading test](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:255) · [Suite log](/tmp/gunther-cleanup/batches/E0-logs/fixture-tests.log) | **47.00 s** |
| 13-paper development harness | `/tmp/gunther-harness/out/*/raw.json` and the matching originals documented under `/tmp/gunther-papers`. Both directories were absent. [Corpus notes](/root/git/Gunther/docs/DOCUMENT_STRUCTURE_PLAN.md:249) | Unmeasured |

If the development harness becomes available, pair each authorized raw recording with its original PDF and run it through the same replay boundary. Raw JSON alone can go through `structured_document(raw, metadata, reviser=None)` for a rules-only structure comparison. The native-text, file-metadata, and crop checks require the matching PDF or equivalent recorded evidence. Development replay uses only the approved development inputs.

## Reproduce the checked fixture run

I used the installed backend virtual environment and an isolated loopback namespace. Run these commands from the repository.

```bash
unshare --user --map-root-user --net /bin/bash -c '
set -eu
ip link set lo up
cd apps/backend
export PYTHONDONTWRITEBYTECODE=1 GUNTHER_DESKTOP_SIDECAR=1 SEMANTIC_SEARCH=false OMP_NUM_THREADS=1 HF_HUB_OFFLINE=1
/usr/bin/time -p .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp "$(mktemp -d /tmp/gunther-e0-pytest.XXXXXX)" \
  tests/test_pdf_fidelity.py tests/test_structure_draft.py \
  tests/test_document_pipeline.py tests/test_docling_upgrade.py'
```

Use the pilot snapshot command from `apps/backend` inside an isolated namespace with the same environment as above. Run it at the starting and candidate revisions with separate output directories and the same fonts and dependencies. It calls the checked-in fixture builders and reader.

```bash
export PYTHONPATH="$PWD/tests:$PWD"
snapshot() {
  /usr/bin/time -p .venv/bin/python - "$1" <<'PY'
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch

from gunther.document_parser import structured_document
from test_pdf_fidelity import build, parser
from test_structure_draft import two_page_paper

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

def save(name, reading):
    record = {
        "manifest": reading.manifest,
        "elements": [asdict(block) for block in reading.elements],
        "passages": [asdict(block) for block in reading.chunks()],
        "assets": [
            {"key": asset.key, "sha256": sha256(asset.data).hexdigest()}
            for asset in reading.assets
        ],
    }
    (out / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(name, "elements", len(reading.elements), "passages", len(record["passages"]),
          "entries", reading.manifest["accounting"])

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
}

# Run once at each revision, using the same environment.
snapshot /tmp/gunther-e0-before
snapshot /tmp/gunther-e0-after
diff -ru /tmp/gunther-e0-before /tmp/gunther-e0-after
```

For each changed reading, review exact source text and source regions in reading order, then list each changed disposition, heading or date with its before and after values and its source-backed reason. Match regions using the recorded document reference, page and span or bounding box. Keep the raw capture for cases where a block is split or joined. A zero unaccounted count is a useful check, and every removal still needs a specific reason. Add the relevant downstream assertions when a batch changes a card, Work, search scope or saved revision. [Omission and accounting records](/root/git/Gunther/apps/backend/gunther/document_model.py:1200) · [Saved revision replay](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:416)

## General rules and what the diff can verify

| Item | General replacement rule | Verification with the available offline corpus |
| --- | --- | --- |
| SC-03 | Build the heading hierarchy from source tags or bookmarks, then local typography, numbering and reading order, preserving printed headings and allowing an empty outline. | **Yes for the exercised layouts.** Diff heading text, parent, level and anchor. The recorded paper and heading-free form supply both sides of the rule. [Heading replay](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:235) |
| SC-04 | Identify run-ins from observed text and style boundaries, and classify front and back matter by evidenced role and placement, recording the disposition and reason for every block. | **Yes for the exercised cases.** Diff roles, anchored notices and omissions. Include the licence, run-in and adjacent body-text fixtures. [Run-in fixture](/root/git/Gunther/apps/backend/tests/test_structure_draft.py:580) |
| SC-05 | Take abstract, outline and passage eligibility from the same reading revision's observed roles, with explicit legacy markers as the fallback. | **Partial.** The diff verifies role, outline and passage changes, including an absent abstract. Use stored-document and old-revision assertions to verify that the card, viewer and retrieval agree. [Control diff](/tmp/gunther-cleanup/batches/E0-logs/abstract-control.diff) · [Published-reading test](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:255) |
| SC-06 | Attach captions, figures and reference entries through declared links or corroborated region ownership, reading order and citation structure, keeping unresolved text readable. | **Yes for reading placement.** Diff parent and region spans, reference entries and crop hashes. The published-reading fixture separately checks the resolved citation links. [Cross-page caption](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:387) · [Reference links](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:255) |
| SC-07 | Bind identifiers and dates to the source's evidence, distinguish publication, preprint and file metadata and publisher from producer, and group Works only with compatible identifiers and corroboration. | **Partial.** The reading diff verifies typed metadata, anchors and origin. Use a multi-source fixture to verify Work grouping and old links when identifiers conflict. [Published metadata and card year](/root/git/Gunther/apps/backend/tests/test_pdf_fidelity.py:295) · [Works test](/root/git/Gunther/apps/backend/tests/test_papers.py:322) |
| SC-09 | Derive margins, columns and tolerances from observed page geometry and local text scale, retaining regions whose placement remains uncertain. | **Yes for the exercised geometry.** Diff order, boxes, region coverage and dispositions. Add development controls with shifted columns, rotated pages and sparse layouts for any rule the current fixtures miss. [Margin fixture](/root/git/Gunther/apps/backend/tests/test_structure_draft.py:396) |
| SC-10 | Keep the document title as printed and replace an editable source title only when its stored provenance marks it as provisional. | **Partial.** The reading diff verifies title wording and anchors. Use capture, rename and reprocess fixtures to verify ownership and stored-title behavior. [Title typography](/root/git/Gunther/apps/backend/tests/test_papers.py:179) · [Provisional title](/root/git/Gunther/apps/backend/tests/test_digests.py:374) |
| SC-16 | Judge a numerical claim by its field, referent, units and supporting provenance, while recognizing format labels by their role. | Use offline Studio output and Checker fixtures. See [Studio claims and covers](#studio-claims-and-covers). |
| SC-17 | Use declared genre as context and require block-specific evidence before promoting prose or omitting text or figures. | **Yes for the exercised cases.** Compare kept body text, absent abstracts and uncaptained figures against the evidence and omission ledger. Genre breadth remains limited to the development fixtures available here. [Unmarked abstract](/root/git/Gunther/apps/backend/tests/test_structure_draft.py:270) · [Uncaptioned pictures](/root/git/Gunther/apps/backend/tests/test_structure_draft.py:290) |

## Studio claims and covers

Give the renderer and Checker one cover model with fields for the title, full subtitle and its citations, and metadata with its provenance. Read older saved covers through that model. Source-cover normalization replaces the metadata field and preserves the subtitle. [Cover renderer](/root/git/Gunther/apps/desktop/src/outputs/deck/parseDeck.ts:187) · [Source-cover normalization](/root/git/Gunther/apps/backend/gunther/studio/science.py:571)

Give numerical claims one identity that includes their field, referent, effective unit and supporting evidence. Resolve the unit before rendering or checking a chart. Preserve it when falling back to a table and when saving or retrying the check. A unit change requires a fresh verdict. Keep chart label roles and each plain-table cell's provenance in the shared representation. [Verdict keys](/root/git/Gunther/apps/backend/gunther/checks.py:23) · [Chart fallback](/root/git/Gunther/apps/backend/gunther/studio/pipeline.py:1834)

| Round 27 reproduction | Expected result |
| --- | --- |
| Standalone cover date `Spring 2026` | The renderer displays the date as metadata and the Checker excludes that field from the claims. |
| A subtitle continues with `because the review began in 2024` | The Checker receives the whole dated claim. |
| `Working monitors \| Cohort` with an uncited count in the first data cell | The citation check flags the count. |
| The same chart values change from `ms` to `s`, then fall back to a table and retry | The unit change requires new verdicts. The fallback displays the effective unit, and retry checks that same unit against the cited evidence. |
| One numeric cell cites a source and an adjacent numeric cell is uncited | The plain-table citation check flags the uncited cell. |
| A wrapped journal-club question has an unindented, uncited continuation | Source-cover normalization preserves the entire question. |
| A continuation in the subtitle carries `[i:1]` | The renderer and Checker both retain the line in the subtitle with its citation. |
| Source metadata is applied twice to a metadata-only cover | The second application leaves the same metadata field and cover text. |

## Proposed order

1. Freeze the development input list and baseline snapshots. Resolve the 13-paper input gap before claiming that gate has passed.
2. **E41d, SC-09** then **E41a, SC-03**. Establish document-local geometry before heading hierarchy consumes it.
3. **E41c, SC-06**. Establish figure, caption and reference ownership before changing dispositions.
4. **E41b first cut, SC-04**, then **E41e, SC-17**, then **E41b second cut, SC-05**. Record dispositions, remove genre-driven overrides, and then share the resulting revision roles across readers.
5. **E42a, SC-07** then **E42b, SC-10**. Establish metadata provenance and grouping before using provenance to govern source titles.
6. **Studio claims and covers, SC-16**. Use the separate offline Studio and Checker regression set.
