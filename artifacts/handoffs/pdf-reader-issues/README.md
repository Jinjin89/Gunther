# Remaining PDF extraction issues

Current anonymous findings after testing `f8e26da`. This replaces the dated initial reviews,
duplicate retest files and identifying screenshots. The evaluation documents remain held out.

## Open problems

1. **Text fidelity remains insufficient.** Fresh reading text still contains replacement
   characters, malformed words and unreliable mathematical symbols. Reporting uncertainty is
   an improvement, but the recovered text is not consistently faithful.
2. **Figure crops can clip content or expand into surrounding text.** Boundary growth can
   include running headers, identifier lines and caption fragments inside a figure image.
   Some edges still cut through content, and words can appear both in the image and in the
   separately rendered caption.
3. **Some numeric citations remain unrecognized.** Resolved bibliography links now exist,
   but `test_pdf_fidelity.py::test_the_stored_document_reads_the_same_everywhere` still fails
   because an expected numeric superscript citation is missing. The failure reproduced in
   isolation on the tested macOS environment.
4. **Publication metadata recovery is incomplete.** Author lists improved, while publication
   dates and years remain missing in some results despite available source evidence.
5. **Drawn checkbox detection is too broad.** Small squares in diagrams, token grids and
   legends are classified as form controls without enough evidence that they belong to a
   form. This creates spurious checkbox candidates and misleading unplaced-form warnings.

Reliable selection preservation on retained real forms was not established by this retest.
Some reporting-form material was omitted with recorded reasons. Test form semantics with
independent fixtures, preserving checked/unchecked states and answer relationships whenever
forms are retained as content.

## Improvements already observed

Physical page counts and document-card outlines improved. Captions carry inline formatting,
omissions have reasons, source accounting reported no unaccounted blocks, and cross-page
figure provenance is retained where observed. Unreliable equation strings are withheld from
trusted text, with equations preserved as images. Image-only equations remain a limitation
for textual retrieval; do not invent transcriptions to remove the warning.

## Verification context

The retest used the rebuilt, installed desktop app and offline parser. The backend run had
1,591 passing tests, one failure described above and three skips; 40 focused reader tests
passed. The full check stopped at the backend failure. The production build and native
signature verification succeeded.

The separate selected-source visibility and save-ordering bug has since been fixed and
covered by 236 backend workflow tests and 71 desktop tests. It is not an outstanding parser
issue. The next presentation-quality task is described in
[Scientific presentations that explain the study](../../../docs/SCIENTIFIC_PRESENTATION_WORKFLOW.md).

Use general fixes and independent development fixtures. Do not inspect the user's held-out
papers, old identifying screenshots, or live library to tailor production rules. Preserve
original files and provenance, and verify the Leadership library stays unchanged when
performing any authorized app validation.
