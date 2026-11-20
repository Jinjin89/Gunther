# Gunther cleanup plan

**FINAL** · Round 3 decisions accepted. Batch 08a includes the approved Ask SSE fixture migration before route removal.

## Added-scope review

### Special cases in production

Production roots checked include `apps/backend/gunther`, `apps/desktop/src`, `apps/desktop/src-tauri/src`, `apps/mobile/lib`, contracts and packaged helpers. Fixture and eval references were traced to see whether they reach an installed entry point. Paths below use repo-relative roots. Keep domain tokens that describe an input format, a declared capability or an observed source attribute.

#### SC-01 · redesign · bundled example libraries and startup seed

- **Where** `apps/desktop/src/atlas.ts:73–381`, `apps/desktop/src/atlasMetadata.ts:5–16`, `apps/desktop/src/App.tsx:123,158,327,568`, `apps/backend/gunther/service.py:6353–6425`, `apps/backend/gunther/main.py:332–334`.
- **What** Startup seeds a fixed knowledge base and desktop merges or falls back to bundled sample IDs. An unreachable mobile form repeats the example.
- **Change** Use server metadata and Contents for any ID, preserve real rows even if their IDs match samples, and move the seed to a repo-only fixture command. Remove the mobile sample with its unreachable screen after the Flutter gate. This carries D-06/B-10/M-01.
- **Evidence** `rg -n 'seed_if_empty|Starter knowledge|CD3D|getKnowledgeBase|metadataToBase' apps`.
- **Behaviour change** Fresh libraries start from their own metadata. Samples appear only in repo fixtures and existing real rows stay available.

#### SC-02 · redesign · examples shipped in authoring prompts and smoke

- **Where** `apps/backend/gunther/studio/prompts.py:108`, `apps/backend/gunther/studio/reference.py:159`, `apps/backend/gunther/studio/smoke.py:11–31`, `apps/backend/gunther/desktop_server.py:894`.
- **What** The active Studio prompts include a named sample topic. A packaged smoke command brings its own test deck.
- **Change** Express the title, evidence and closing-slide requirements in rules that apply to any material. Keep format grammar, move literal examples to format/eval fixtures, and pass smoke input from the repo command (B-11). Keep deck, report and journal-club tests.
- **Evidence** `rg -n -i 'cool roofs|DECK_FORMAT|STUDY_DECK_FORMAT|FIXTURE' apps/backend/gunther/studio`.
- **Behaviour change** Authoring prompts describe the job independent of the sample material. The same offline smoke runs with an explicit fixture.

#### SC-03 · redesign · headings and levels driven by paper vocabulary

- **Where** `apps/backend/gunther/structure_draft.py:47–112,286–292,1547–1590`, `apps/backend/gunther/pdf_text.py:21–71`, `apps/backend/gunther/processing.py:346–389`.
- **What** The native-text path recognizes lexical labels. The canonical draft uses IMRaD-named headings to anchor which other headings become subsections.
- **Change** Use bookmarks/tags first, then comparable local font, spacing, indentation, numbering and reading order. Treat language labels as weak role clues after heading detection. Preserve each title as printed and allow an unheaded document or sections outside IMRaD. Phase E verifies the same corpus before and after the change.
- **Evidence** `rg -n '_HEADING_KINDS|_IMRAD|_NAMED_HEADINGS|looks_like_heading' apps/backend/gunther`.
- **Behaviour change** The same typographic and ordering cues produce the same hierarchy for unfamiliar labels. A document may have no outline.

#### SC-04 · redesign · run-ins, publication notices and suppressible back matter

- **Where** `apps/backend/gunther/structure_draft.py:73–126,477–501,618–627,1335–1366,1633–1669`, `apps/backend/gunther/document_structure.py:59–73,293–319,341–388`.
- **What** Lexical matches can skip text during reflow and can put an entire heading scope into drop. A paper section named “Contributions” can take that branch.
- **Change** Recognize a run-in from typography and the following text boundary. Require placement, semantic role or independent layout evidence before excluding content. Keep licence/date/availability details with origin and anchors. Record a disposition for every block. Weak hits stay readable and uncertain. Run this in Phase E.
- **Evidence** `rg -n '_RUN_INS|_LICENCE|_DATES|PUBLICATION_NOTICE|run-in:back-matter' apps/backend/gunther`.
- **Behaviour change** The reading keeps ambiguous prose. Proven back matter has an explicit reason and its source remains accessible.

#### SC-05 · redesign · abstract recognition and retrieval exclusions recur downstream

- **Where** `apps/backend/gunther/content.py:270–284`, `apps/backend/gunther/document_structure.py:423–425,605–679,735–744`, `apps/backend/gunther/papers.py:285–320,341–373,515–523`.
- **What** The text path suppresses vectors before an English or Chinese abstract among the first 60 blocks on pages 1–2. Paper details classify abstract and headings again.
- **Change** Read roles and outline from the same revision, including typed metadata or verified bibliography. For pre-role editions accept explicit legacy heading markup and retain ambiguous text. An absent abstract or heading stays absent. Preserve revision quotes, membership and citations. Run this in Phase E. Phase A only removes the fabricated abstract under NM-05.
- **Evidence** `rg -n '_mark_front_matter|mark_abstract|in_reference_section|_abstract\(|_outline\(' apps/backend/gunther`.
- **Behaviour change** The card, viewer, search and Ask see one observed structure. Old pinned revisions keep their saved quotes.

#### SC-06 · redesign · figure/caption and reference vocabularies override attachments

- **Where** `apps/backend/gunther/structure_draft.py:39–50,137–145,504–509,943–980`, `apps/backend/gunther/document_structure.py:65–68,785–841,984–1008`, `apps/backend/gunther/documents.py:243–264`.
- **What** Two caption grammars and a number/name-plus-year reference matcher can decide what counts as figure or bibliography content.
- **Change** Prefer declared caption links, HTML figure parents and PDF region ownership. Use reading order, matching label/number and geometric attachment to classify untagged material. Require a consistent list/citation relationship for references. Retain unresolved blocks and avoid moving a numbered sentence into a figure or bibliography merely from its wording. Keep identifiers and exact reference text. Run this in Phase E.
- **Evidence** `rg -n '_CAPTION|CAPTION_LABEL|_reference_entry|reference-entry' apps/backend/gunther`.
- **Behaviour change** An explicit link keeps its owner. An unresolved caption or numbered sentence remains visible until the source supports a role.

#### SC-07 · redesign · identifier/date shortcuts at the metadata edge

- **Where** `apps/backend/gunther/papers.py:41–57,120–180,476–524,556–575`, `apps/backend/gunther/document_structure.py:102–160,341–368`.
- **What** A front-page arXiv match can win over a publication date. A title match can group papers if identifier lookup misses, including a differing identifier. PDF file dates and producer already have distinct names.
- **Change** Keep DOI and arXiv parsing as identifier conventions. Verify the identifier belongs to this document, store preprint dating separately, and prefer anchored or declared publication dates. Fall back to title matching only with corroboration and compatible IDs. Keep current Source and Work links readable during any reprocess. Run this in Phase E.
- **Evidence** `rg -n 'ARXIV|YEAR_NOTICE|MIN_TITLE_KEY|/Subject|fileCreated|fileProducer' apps/backend/gunther`.
- **Behaviour change** Publication and preprint dates keep separate provenance. Work identity stays distinct when authoritative IDs disagree. Existing links still open.

#### SC-08 · keep · format-defined structure, observed glyphs and resource bounds

- **Where** `apps/backend/gunther/document_structure.py:102–160,199–290`, `apps/backend/gunther/document_parser.py:553–565`, `apps/backend/gunther/asset_service.py:166–224,333–402`, `apps/backend/gunther/pdf_glyphs.py:1–16`, `apps/backend/gunther/structure_model.py:180–196`.
- **What** Declared metadata, observed font and glyph evidence, and decompression/conversion/request budgets describe the input and the resources it needs.
- **Change** Keep these general conventions. DOCX/EPUB/HTML styles, namespaces, link schemes and explicit PDF bookmarks belong to their readers. Resource limits may return a partial or unreviewed result with provenance. Keep the complete original.
- **Evidence** `rg -n 'pdf-bookmark|page.*budget|MAX_BLOCKS|MAX_CHARS|CreationDate|Producer' apps/backend/gunther`.
- **Behaviour change** Each format retains its declared structure, and bounded reads can report partial results with the original still available.

#### SC-09 · redesign · fixed geometry cutoffs hide layout variation

- **Where** `apps/backend/gunther/structure_draft.py:36–37,512–544,868–878,1350–1366`, `apps/backend/gunther/document_structure.py:436–475,785–835,966–983`, `apps/backend/gunther/figure_crops.py:21–25`.
- **What** Fixed page bands, one-page switches and crop cutoffs affect dispositions and ownership. A short document can contain real notes and full-width marginal text.
- **Change** Derive body columns and edges per page from observed text and repeated regions. Relate tolerances to normalized page/column and local line sizes. Use minimum evidence before dropping. Keep named allocation or candidate bounds with their stated reason. Test columns, shifted margins, mixed orientations and sparse pages. Run this in Phase E.
- **Evidence** `rg -n 'TOP, BOTTOM|margin-column|one_page|850_000|def recover_figure_regions|REACH' apps/backend/gunther`.
- **Behaviour change** Equivalent reading structure survives a margin shift or page orientation change. Measurable allocation budgets remain explicit.

#### SC-10 · redesign · title provenance inferred by string resemblance

- **Where** `apps/backend/gunther/digest.py:504–532`, `apps/backend/gunther/papers.py:585–604`, `apps/backend/gunther/pdf_text.py:157–193`.
- **What** Title comparison to camera names or file stems can overwrite a label the user entered. The native title reader can replace the source's letter case.
- **Change** Carry where the title came from when capture or rename saves it. Allow a document-declared title or model suggestion to replace only a recorded provisional label. Old ambiguous names stay as saved. Preserve the exact document wording and use a visible suggestion for user-owned names. Run this in Phase E.
- **Evidence** `rg -n 'PLACEHOLDER_TITLE|_named_after_its_file|title_by_font|_title_case' apps/backend/gunther`.
- **Behaviour change** The reader keeps the author's spelling and the user's saved label. Only a known provisional title is eligible for automatic replacement.

#### SC-11 · redesign · slide/report titles judged by language lists

- **Where** `apps/backend/gunther/studio/titles.py:12–211`, `apps/backend/gunther/studio/format.py:480–492,733–753`, `apps/backend/gunther/studio/pipeline.py:1352–1413`.
- **What** English/CJK word and character counts, verbs and digits can decide whether a title triggers a semantic rewrite.
- **Change** In Phase A, keep nonblank, length, layout, citation and readability checks. Fold any semantic title check into a reviewer request the path already makes. Where that request is absent, drop the word lists and use the structural checks. Keep the existing model-call budget and title locks.
- **Evidence** `rg -n 'CJK_VERBS|COMMON_VERBS|LABEL_TITLE|is_topic_title' apps/backend/gunther`.
- **Behaviour change** Titles use language-independent structural checks and existing reviewer results when available. A review failure follows the existing outcome.

#### SC-12 · redesign · per-language and per-domain text rules decide what gets indexed or named

- **Where** `apps/backend/gunther/content.py:26–89,92–164`, `apps/backend/gunther/library_topics.py:45–80,184–214,217–271`, `apps/backend/gunther/extraction.py:50–105`.
- **What** English-heavy word lists and caps/length rules affect embedding, clustering and names. In extraction an uppercase identifier can become `Gene`.
- **Change** In Phase E, use observed roles for reading and indexing, model context bounds for passage size and document frequency for term display. Keep Unicode tokenization and exact text search. In Phase A under NM-03, remove domain guessing and the prose stand-in. Parse explicitly entered triples as user data.
- **Evidence** `rg -n 'STOPWORDS|MIN_EMBED_WORDS|GENERIC|marker_of|_infer_type' apps/backend/gunther`.
- **Behaviour change** Search still returns exact text and useful terms. Domain types and section names come from evidence, explicit input or an available model.

#### SC-13 · redesign · provider identifiers steer feature code

- **Where** `apps/backend/gunther/api.py:460–478,890–907`, `apps/backend/gunther/main.py:177–193`, `apps/backend/gunther/retranscribe.py:906–932`, `apps/desktop/src/models/SpeechSettings.tsx:209–232,255–266`, `apps/desktop/src/components/LectureRecorder.tsx:135–141,970–984`, `apps/mobile/lib/features/capture/presentation/capture_view_model.dart:1170`.
- **What** Health, live re-listening, retranscript, settings and clients branch on vendor names for capabilities and display. The binder also hardcodes cross-registry key selection.
- **Change** In Phase B, resolve adapter features such as timestamped words, context support and probe/display status once. Drive each operation from that result. Keep existing role/region/credential priority and saved setting aliases. Leave mobile-visible speech behavior untouched until the Flutter gate.
- **Evidence** `rg -n 'kind (==|!=|===|!==).*sensevoice|kind == "qwen"|provider == .*sensevoice|_shared_speech_keys' apps`.
- **Behaviour change** Equivalent capabilities give equivalent behavior. Regional credentials, legacy key preferences and gated mobile wire fields keep their meaning.

#### SC-14 · keep · provider dialects, file formats and read-only decoding

- **Where** `apps/backend/gunther/model_profiles.py:202–216`, `apps/backend/gunther/speech_registry.py:118–137,348–417`, `apps/backend/gunther/realtime.py:112–178,1182–1259`, `apps/backend/gunther/portable_documents.py:217–321`, `apps/backend/gunther/processing.py:345–365`, `apps/desktop/src/items/sourceContent.ts:127–140,222–255`.
- **What** Provider dialects and saved formats have legitimate protocols. Repeated format switches also appear in the worker. Web reading uses transport limits and generic HTML handling.
- **Change** Keep provider aliases in adapters, standard identifiers/metadata in readers, and stored-format decoding at the edge. Move worker dispatch behind one format reader. Keep supplied audio/sample type or media sniffing separate from how the source is rendered. External provider/API URLs are adapter data. The pairing rules and native OS selectors serve platform or transport requirements.
- **Evidence** `rg -n 'profile_for|qwen_live_id|archive_document|LEGACY_ASSET_PARSER|recording.*[Mm]arker' apps packages`. See the site-independent response and generic HTML handlers at `apps/backend/gunther/web_capture.py:47–74,513–522`.
- **Behaviour change** Readers still open the same stored bytes. After eligible consolidation the worker receives one typed result for each current input format.

#### SC-15 · keep · fixtures with verified test-only callers

- **Where** `apps/desktop/src/outputs/deck/testTemplate.ts:1–24`, `apps/desktop/src/outputs/slides/gallery.fixture.md:1–9`, `apps/desktop/src-tauri/src/library_location.rs:224–236`, `scripts/smoke_studio_flow.mjs:47–96,280–310`.
- **What** The gallery, deck template and native example have test or repo-smoke callers. The parser's worked examples in `papers.py:147,302` accompany production rules addressed in SC-04/07.
- **Change** Keep examples within test or repo-command boundaries. Move the parser comments' worked examples into those regressions as the associated rule changes.
- **Evidence** `rg -n 'testTemplate|gallery\.fixture|deck\.short|deck\.max|smoke_studio_flow' apps scripts package.json`. Runtime reach was checked via the fixture imports, smoke command and the Rust test module.
- **Behaviour change** Regression material remains available to fixtures and repo tooling.

#### SC-16 · redesign · numerical citation safety depends on magnitude and spelling

- **Where** `apps/backend/gunther/studio/format.py:768–798,851–856,876–935`, `apps/backend/gunther/studio/pipeline.py:2192–2216`.
- **What** Citation logic exempts small integers and year-shaped strings, and treats some matches as sufficient from digit count, repetition or decimal points.
- **Change** In Phase E, review remaining numerical exemptions using known field, context, units and provenance. Keep deterministic parsing for format validation. Phase A removes the post-check insertion under NM-11. Phase E adds neither a citation-candidate workflow nor model calls.
- **Evidence** `rg -n '_countable|_telling|cite_(stated|repeated)_numbers' apps/backend/gunther/studio`.
- **Behaviour change** A small count or a year can stay in the existing check scope when it conveys a fact. Numeric overlap alone confers no support.

#### SC-17 · redesign · guessed genre changes which content survives

- **Where** `apps/backend/gunther/structure_draft.py:1245–1268,1285–1300,1438–1451,1617–1623`.
- **What** A bibliography plus a long opening block can mark a document as a paper. The draft can promote that block to abstract and drop an uncaptioned small image based on the guessed genre. Landscape counts also affect slide grouping.
- **Change** Keep declared/source-defined profiles as context. Classify individual blocks from attachment, position, typography and repeated furniture evidence. A guessed genre can guide review but alone cannot drop a block or turn opening prose into an abstract. Run the genre and disposition work in Phase E. Remove false-abstract fallback in Phase A.
- **Evidence** `rg -n 'def _document_type|paper and not large|len\(b.text\) >= 300|self.type == "slides"' apps/backend/gunther/structure_draft.py`.
- **Behaviour change** A landscape report keeps its own structure. A small genuine figure or a paper without a printed abstract remains readable with the right disposition.

### No-model stand-ins

Generated work uses three outcomes. A missing role/key points to the relevant Settings pane. A failed attempt keeps user input, saved versions and diagnostics available for Retry. A bounded or optional check may leave a draft readable with a visible incomplete status. Read-only facts, retrieval hits, format validation and excerpts retain their own labels and provenance.

#### NM-01 · remove · Ask's local reply after no model or failure

- **Where** `apps/backend/gunther/service.py:2109–2200,2510–2563`, `apps/backend/gunther/conversation.py:27–95`, `apps/backend/gunther/main.py:255`, `apps/backend/gunther/api.py:1409–1434`.
- **What** The missing-model and failed-model branches assemble quotations and claims into an answer. The streaming route can already send a terminal failure.
- **Change** Remove the local answer producer and its injection. Emit an actionable missing-model result with Settings, or an error with Retry when the model failed. Preserve question drafts, cancelled/failed turns, search tools, existing stored local replies, and configured-model responses. Keep trace diagnostics in the installed UI.
- **Evidence** `rg -n 'LocalKnowledgeResponder|responder_mode|self\.responder' apps/backend/gunther`.
- **Behaviour change** Home and Library show Settings or Retry, keep the question and expose search results without saving a generated local reply.

#### NM-02 · remove · desktop's separate offline Ask reply

- **Where** `apps/desktop/src/pages/SessionWorkspace.tsx:686–693,829–840,934–948`, `apps/desktop/src/pages/useHomeAsk.ts:130–191`.
- **What** When Library cannot open the backend it makes a temporary session and replies from displayed takeaways. Home already preserves a failed draft.
- **Change** Keep the real draft and offer reconnect/Retry. Remove the local answer and fake history with SC-01. Stored user library data and source browsing remain available under their current storage rules.
- **Evidence** `rg -n 'localReply|localResponseTimer|localSession\(' apps/desktop/src`.
- **Behaviour change** Reconnect resumes the real draft. Existing saved sessions and browsing remain usable.

#### NM-03 · redesign · rule-based assertion extraction substitutes for Analysis

- **Where** `apps/backend/gunther/extraction.py:50–156`, `apps/backend/gunther/service.py:917–973`, `apps/backend/gunther/processing.py:496–499,549–553`.
- **What** Missing Analysis or a model error invokes English prose patterns, guessed types and fixed confidence. Foreground import and queued analysis both reach the extractor.
- **Change** Preserve each capture and its searchable text before analysis. Missing key or failed analysis is a visible job state with Retry and no new heuristic claims. Keep explicitly structured user assertions as a separate parser operation and keep old assertions readable. Protect existing mobile submission/receipt behavior under the Flutter gate.
- **Evidence** `rg -n 'LocalExtractor|fallback\.extract|extract_assertions' apps/backend/gunther`.
- **Behaviour change** Captures stay searchable. Model-dependent claims stay absent until Analysis succeeds, and a queued failure can be retried.

#### NM-04 · remove · manufactured topic overviews overwrite a previous synthesis

- **Where** `apps/backend/gunther/library_topics.py:283–331,399–446`, `apps/backend/gunther/knowledge_api.py:215–231`, `apps/desktop/src/contents/SectionPanels.tsx:57–95`.
- **What** The overview writer saves a local abstract digest when Analysis is absent, errors or returns invalid citations. That write can replace a prior synthesis.
- **Change** Keep filing, source list and actual abstracts visible. Require Analysis to write a new synthesis. Return Settings or Retry and retain the last successful overview. Read previously saved `local` rows with their original provenance.
- **Evidence** `rg -n 'LocalTopicWriter|method = writer.method|_opening\(paper.abstract|writeTopicOverview' apps`.
- **Behaviour change** The last successful synthesis stays readable, with a distinct Settings or Retry action for a new attempt.

#### NM-05 · redesign · opening paragraphs masquerade as a paper's abstract

- **Where** `apps/backend/gunther/papers.py:285–338,517–521,527–553,615–632`, `apps/backend/gunther/structure_draft.py:1417–1451`, `apps/desktop/src/items/views/PaperCard.tsx:32–44`.
- **What** Both structure drafting and the paper details job can fill `abstract` with opening paragraphs when the source lacks an abstract. The paper card and export display that value as Abstract.
- **Change** In Phase A, remove both paths that fill an absent abstract with opening prose. Keep explicitly marked abstracts, existing unlabelled source passages and old saved rows. Use the existing AI Summary status. Phase E owns new reading rules.
- **Evidence** `rg -n '_gist\(|facts\.abstract|## Abstract|len\(block.text\) >= 300' apps/backend/gunther/{papers,structure_draft}.py`.
- **Behaviour change** A new reading with no actual abstract leaves that field empty. Existing saved versions and the original prose stay readable.

#### NM-06 · redesign · Ask planning fallback and ungraded evidence

- **Where** `apps/backend/gunther/agent.py:886–909,1131–1151,1243–1267,1609–1631`.
- **What** Ask makes a first-tool action when planning fails. A failed grading pass can keep everything or supply empty findings. Research framing already exposes failure.
- **Change** Keep the first-tool planning fallback as control flow and record its failure in the trace. The model still writes the answer. Keep required grading failures visible as Retry. If optional grading fails, pass the raw scoped hits as ungraded and preserve the failure in events and trace. Keep explicit Compare and Research.
- **Evidence** `rg -n 'The planning step failed|The grading step failed|A grader that cannot answer' apps/backend/gunther/agent.py`.
- **Behaviour change** The answer can still use model-written text after the existing fallback search. Trace shows the planning failure, and raw hits stay ungraded.

#### NM-07 · redesign · audit failures and failed Check caches look like empty results

- **Where** `apps/backend/gunther/agent.py:1699–1726,1821–1872,2107–2109`, `apps/backend/gunther/outputs.py:1305–1335,1462–1487,1833–1852`, `apps/backend/gunther/service.py:4927–4988`.
- **What** An audit error can become an empty list. Failed or partial check data can share the text hash used to reuse a Check.
- **Change** Store audit unavailability/partial coverage separately from an empty verdict. Offer Retry for unavailable or unchecked work and merge just the unresolved claims. Reuse prior successful coverage with the same text and evidence. Keep deterministic syntax, cross-reference and renderer checks independently labeled.
- **Evidence** `rg -n 'The audit step failed|Check unavailable|if digest in known|def check_output' apps/backend/gunther`.
- **Behaviour change** Retry processes the unchecked claims and retains verified results with the same text and evidence. Empty successful findings keep their meaning.

#### NM-08 · keep · authored draft and rendered fallback after optional review

- **Where** `apps/backend/gunther/studio/pipeline.py:942–985,1240–1279,1352–1413,1644–1692,2160–2225`, `apps/backend/gunther/slide_review.py:310–324`, `apps/backend/gunther/service.py:3329–3347`.
- **What** The generated draft, measured geometry, and optional critique have different failure modes. Studio preserves an earlier authored cover if alignment fails and currently only logs the failure.
- **Change** Keep authored words, saved titles, render fallback and actual coverage. Surface a failed critic, repair or align operation through an existing status where one is already defined. No new review state, UI or retry action.
- **Evidence** `rg -n 'verdict="unavailable"|fallback_title|Geometry checks|Scientific review unavailable' apps/backend/gunther`.
- **Behaviour change** Existing drafts and rendered text stay available. Where a status already exists, it reflects the failed operation.

#### NM-09 · keep · actual source structure and transcription during unavailable semantic review

- **Where** `apps/backend/gunther/document_parser.py:377–414`, `apps/backend/gunther/structure_model.py:243–283`, `apps/backend/gunther/retranscribe.py:939–966`, `apps/backend/gunther/api.py:1012–1043`.
- **What** Structure assembly retains original blocks and its review state. A timed transcript groups by window if the Analysis paragraph step fails or is absent.
- **Change** Keep native/OCR reading and actual timed ASR words. Preserve recorded structure review status. Report time windows as ungrouped when the Analysis step is unavailable. Use existing status fields and controls.
- **Evidence** `rg -n 'StructureStatus\("(needs-key|too-long|failed)"|paragraphs_by_window|group_paragraphs' apps/backend/gunther`.
- **Behaviour change** Time windows are visibly distinct from model-grouped paragraphs. The current reader keeps the original text and stored review status.

#### NM-10 · keep · model jobs with explicit failure already

- **Where** `apps/backend/gunther/digest.py:414–453,520–530`, `apps/backend/gunther/lecture.py:43–61`, `apps/backend/gunther/service.py:1738–1758`, `apps/backend/gunther/brief.py:292–307`, `apps/backend/gunther/tts_narration.py:202–278`.
- **What** Summary, brief, lecture and narration paths already surface missing role or failure. Reading filed/quoted text and supplying a literal default name are plain operations.
- **Change** Keep existing failure and recovery behavior for AI Summary, lecture summary, conversation brief and narration. Keep literal input labels and read-only content on their existing paths.
- **Evidence** `rg -n 'DigestError|LectureSummaryError|No model to update|StructureTooLong|needs a model to describe' apps/backend/gunther`.
- **Behaviour change** Existing failure states, stored successes and controls keep their current roles.

#### NM-11 · redesign · report formatting can insert a citation after fact checking

- **Where** `apps/backend/gunther/studio/format.py:876–935`, `apps/backend/gunther/studio/pipeline.py:2192–2216`.
- **What** Studio adds citations after verification when the same digits appear in cited text or in a passage. A passage about `37.5 ms` can donate a reference to a statement giving `37.5%`.
- **Change** In Phase A, delete the post-check numeric citation insertion. Keep established references and current checking as-is. SC-16 separately covers remaining numerical exemptions in Phase E.
- **Evidence** `rg -n 'self\.verify|cite_(stated|repeated)_numbers|self\.assemble' apps/backend/gunther/studio/pipeline.py`.
- **Behaviour change** Post-check formatting no longer appends citations from numeric overlap.

#### NM-12 · retire · older writer uses earlier plans and empty reviews after failure

- **Where** `apps/backend/gunther/skill_runner.py:988–1017,1098–1112,1274–1287,1988–1995`.
- **What** The earlier writer keeps its first outline after a failed repair and initializes an empty review when review fails. Separate design/revision failures can keep the already authored plan and words.
- **Change** Retire these branches with the earlier writer in 02–07. Carry format decoding, saved plan/locks and useful scenario tests into Studio. Keep an explicitly unavailable or failed check distinct from a completed review.
- **Evidence** `rg -n 'Fixing the outline failed|review = OutlineReview\(|return attempted\(planned\)|return drafts' apps/backend/gunther/skill_runner.py`.
- **Behaviour change** Old artifacts keep their saved content. New revisions use Studio's visible error, partial review and resume behavior.

### Documentation inventory

The inventory covers the 51 baseline files plus the supervisor decisions added to the checkout. The target includes root `README.md` and twelve files in `docs/`. Keep `ARCHITECTURE.md`, `DEVELOPER.md`, `DESIGN_SYSTEM.md`, `LIBRARY.md`, `CAPTURE.md`, `ASK.md`, `OUTPUTS.md`, `STUDIO_FORMAT.md`, `SETTINGS.md`, `OPERATIONS.md`, `FACTS_AND_STEPS_PLAN.md` and `CAPTURE_TRAY_STATES.svg`. Use English and area names for the guides. Each merge carries rules and behavior verified after the implementation phases. Git history preserves the former implementation steps, time-stamped results and abandoned ideas.

| File in `docs/` | Verdict | Evidence and disposition |
| --- | --- | --- |
| `ACCEPTANCE_REPORT_V3_CN.md` | Delete | Lines 3–5 identify schema v10. Sections at 231 and 270 record past verification and release candidates. Keep current recovery instructions in `OPERATIONS.md` and test proofs in the suites. |
| `ACTIVITY_PLAN.md` | Merge → `CAPTURE.md` | Lines 98–172 specify the live Activity contract now in `apps/backend/gunther/activity.py:107` and `apps/backend/gunther/activity_api.py:38–67`. Carry concurrent jobs, location, Stop and dismissal. Drop 173–453 and 490–495 implementation history (S-07). |
| `ARTIFACT_AGENT_PLAN.md` | Merge → `OUTPUTS.md` | Lines 3–7 mark it historical and 749–1040 describe the contracts. Carry validated work/turn/version, evidence and Stop/resume rules from `apps/backend/gunther/artifact_agent.py:646,1489` and `apps/backend/gunther/artifact_workspace.py:1094`. Point the Facts and Steps plan and code docstrings to the living guide. |
| `ARTIFACT_QUALITY_GOAL.md` | Merge → `OUTPUTS.md` | Lines 3–8 and 128–185 identify the shipped Studio design. Carry the live material → story → draft → checks pipeline. Move any current eval command/threshold explanation to `apps/backend/evals/quality/README.md`. Replace old references in the eval and Studio package. Drop the older model runs at 321 onward. |
| `ASK_AGENT.md` | Merge → `ASK.md` | Lines 8–118 and 193–257 cover the current loop, citations, research and stream. Update failure, explicit skills and remaining route descriptions against `apps/backend/gunther/agent.py:912` and `apps/backend/gunther/api.py:1329–1460`. |
| `ASK_RESEARCH_PLAN.md` | Merge → `ASK.md` | Line 3 marks the phases built and lines 181–357 describe the inputs and saved brief. Preserve research budget/stop and check rules that still hold. Drop old auto-skill experiment, phase instructions and scoring logs. |
| `CAPTURE_AI_SUMMARIES.md` | Merge → `CAPTURE.md` | Lines 5–26 explain concurrent capture and 27–71 cover summary status. Carry the shared capture and summary workflow. Check current behavior at `apps/desktop/src/components/AtlasUtilities.tsx:363–422,696` and `apps/backend/gunther/digest.py:414–453`. |
| `CAPTURE_MENU_BAR_DESIGN_CN.md` | Merge → `CAPTURE.md` | Lines 14–30,72–138 describe shortcuts, tray states and durable close/quit. Check the current menu against `apps/desktop/src-tauri/src/capture_shell.rs:274–460` and carry the active choices and SVG link at line 76. |
| `CAPTURE_TRAY_STATES.svg` | Keep | Title and description at lines 1–3 cover the tray states present in `apps/desktop/src-tauri/src/capture_shell.rs:274–338`. Move its sole guide reference from `CAPTURE_MENU_BAR_DESIGN_CN.md:76` as part of that merge. |
| `CLEANUP_ARCHITECTURE.md` | Delete | Sections 1–5 are review evidence and proposed batches. This plan and the batch records carry its execution decisions. |
| `CLEANUP_DECISIONS.md` | Delete | Lines 8–135 are supervisor decisions and work instructions for this cleanup. Keep acceptance through the final batch record. |
| `CLEANUP_REVIEW.md` | Delete | Findings and batch plan at lines 52–457 are cleanup history. Keep closed behavior and regressions in their current guides/tests. |
| `CLEANUP_RULES.md` | Delete | Lines 1–147 direct two completed reviews. `AGENTS.md:3–58` carries the ongoing rules. |
| `DESIGNED_SLIDES_PLAN.md` | Merge → `STUDIO_FORMAT.md` | Line 3 marks P1–P4 built. Lines 51–169 and 233 onward cover stored HTML, editable exports and style. Verify current format/guard/exports at `apps/backend/gunther/studio/design.py:253–309` and `apps/desktop/src/outputs/deck/exportPptx.ts`. |
| `DESIGN_SYSTEM.md` | Keep | Lines 39–104 and 170–258 describe the active visual tokens, readable Markdown and accessibility. Verify the current CSS and shortcuts. Consolidate stable style rules from presentation notes where they affect UI. |
| `DEVELOPER.md` | Keep | Lines 5–42 describe traces, logs and jobs used by `apps/desktop/src/developer/DeveloperSettings.tsx:86–126` and `apps/backend/gunther/developer_api.py:73–126`. Remove references to the retired Method control. |
| `DOCUMENT_STRUCTURE_PLAN.md` | Merge → `LIBRARY.md` | Lines 41–232 specify representation, draft/review, and assembly now used at `apps/backend/gunther/document_parser.py:377–414`. Carry the implemented element/revision contract and honest limitations. Replace incoming links in `document_model.py`, `document_parser.py`, `structure_model.py` and `papers.py`. |
| `FACTS_AND_STEPS_PLAN.md` | Keep | Lines 3–6 mark the agreed unbuilt plan. Update the link to the Artifacts guide and the owners/file references at 397–465 after the splits. Keep the future work clearly marked as agreed and unbuilt. |
| `HOME_SEARCH_PROPOSAL.md` | Merge → `ASK.md` | Lines 3 and 50–72 describe the built split between finding results and asking a question. Carry the current Home entry, session filing and web scope. Check `apps/desktop/src/pages/HomePage.tsx:145,210`. |
| `KNOWLEDGE_AND_AGENT_UPGRADE_IMPLEMENTATION.md` | Merge → `LIBRARY.md` | Lines 5–71 and 94–111 cover canonical blocks, local parser packaging and source-defined structure. Move the verified parser readiness and retrieval behavior, with package boundaries linked from `ARCHITECTURE.md`. Retire the dated verification and release notes. |
| `KNOWLEDGE_AND_AGENT_UPGRADE_PLAN.md` | Merge → `LIBRARY.md` | Lines 3 and 257–316 concern the old rollout. Carry durable revision, scoped retrieval and append-only compatibility from 52–222, after checking actual code. Agent-facing behavior can point to `ASK.md`. |
| `KNOWLEDGE_BACKEND_V4.md` | Merge → `ARCHITECTURE.md` | Lines 22–42,65–206 give the store, retrieval, paper and Docling boundaries. Update old schema/verification labels from `apps/backend/gunther/migrations.py:687` and the final pipeline. Put durable data boundaries in `ARCHITECTURE.md` and source structure and parser status in `LIBRARY.md`. |
| `LIBRARY_CONTENTS_PLAN.md` | Merge → `LIBRARY.md` | Line 3 says Contents is built. Carry tree/filing rules and stable order from 18–74,108–161 as confirmed by `apps/backend/gunther/knowledge_api.py:113–147`. Drop the phase checklist and progress log. Correct `Topics/` to the current `Sections/` writer (S-08). |
| `LIBRARY_FOLDERS.md` | Merge → `LIBRARY.md` | Lines 42–168 describe disk format and recovery. Check actual root and `Sections/` generation at `apps/backend/gunther/library_folders.py:513,539`, and preserve old folder/read migration guidance. |
| `LIVE_TRANSCRIPT_PLAN.md` | Merge → `CAPTURE.md` | Lines 3 and 143–281 describe save and transcript protocol. Carry durable audio, pause/reconnect, optional corrections and retranscription through `apps/backend/gunther/api.py:854–1004`. Include v1 only if the Flutter gate leaves it installed. |
| `OPERATIONS_AND_RECOVERY_CN.md` | Merge → `OPERATIONS.md` | Lines 111–229 and 252–385 are the cold backup, verification, restore, recording and phone recovery runbook. Update schema/version, current safe commands and space/error behavior from `scripts/backup_gunther.py:498,729` and the runtime. |
| `OUTPUTS.md` | Keep | Lines 229–373 give saves, versions, readers, reuse, rendering and stream limits. Update the old Skills whole-build narrative at 29–181 with the unified Studio path and the retained rebuild/revise/Check controls. Fold in valid Artifacts contract details. |
| `OUTPUTS_PLAN.md` | Merge → `OUTPUTS.md` | Line 3 marks all seven phases built. Preserve only unique saved-format/replay or version rules. The progress log at 403 onward and work instructions are completed (S-06). |
| `PAPER_PARSING_PLAN.md` | Merge → `LIBRARY.md` | Lines 13–45 describe native/Docling editions, eligibility and attachment rules now in `apps/backend/gunther/processing.py:346–457` and `apps/backend/gunther/document_structure.py`. Keep verified behavior and limits. Keep named PDFs in test/eval material. |
| `PRESENTATION_BATCHES_0_2.md` | Merge → `STUDIO_FORMAT.md` | Lines 7–21 record stable outline IDs, old design markers and style effects. Carry the saved-format reader and current bounds. Retire the comparison log at 23 onward. |
| `PRESENTATION_BATCHES_3_5.md` | Merge → `OUTPUTS.md` | Lines 12–54 record real-render snapshots, bounded layout repair and unchanged citation/text rules. Check `apps/backend/gunther/slide_review.py:226–448` and preserve the renderer/PDF boundary. Move still-current packaging notes to `OPERATIONS.md`. |
| `PRESENTATION_COMPOSER_PLAN_CN.md` | Merge → `STUDIO_FORMAT.md` | Lines 11–41 specify the content/layout split and element tree. Carry formats still read by `apps/desktop/src/outputs/slides/scene.ts` and `apps/backend/gunther/slide_scene.py`. Drop the implementation log. |
| `PRESENTATION_CONTENT_DESIGN_PLAN_CN.md` | Merge → `STUDIO_FORMAT.md` | Lines 11–62 describe persisted page design, graphics and theme boundaries. Carry stable records/validation that survive engine retirement. Retire helper workflow and verification at 76 onward. |
| `PRESENTATION_GRAPHICS_COMPOSER_PLAN_CN.md` | Merge → `STUDIO_FORMAT.md` | Lines 13–47 specify graphics referencing existing values and blocks plus fallback. Keep still-readable scene contracts and typed boundaries. Remove the completed assignments and build reports. |
| `PRESENTATION_IMPROVEMENT_PLAN.md` | Merge → `OUTPUTS.md` | Line 3 says batches 0–5 shipped. Carry current evidence and bounded repair at 18–34, with the actual renderer and version owners. Omit the build sequence and gallery handoff. |
| `PRESENTATION_QUALITY_PLAN_CN.md` | Merge → `STUDIO_FORMAT.md` | Lines 28–52 describe style, construct and presentation density as independent choices. Keep these where implemented and retire old layout counts, tool comparisons and future work. |
| `PRESENTATION_SKILLS_RESEARCH.md` | Delete | Lines 13–43 and 60–155 compare external workflows and propose changes. The verified format/renderer facts in 44–59 belong in the two living Outputs guides. Delete after checking those facts are covered there. |
| `PRESENTATION_STRUCTURE_RESEARCH.md` | Delete | Lines 21–44 and 60–137 are source research, proposal and eval priorities. Carry present reasoning-plan/evidence rules from 45–59 into `OUTPUTS.md` if missing after the merges. |
| `PRESENTATION_VISUAL_LANGUAGE_PLAN_CN.md` | Merge → `STUDIO_FORMAT.md` | Lines 5–19 describe the distinction between theme and element relationships. Transfer only live hierarchy, ordering and rendering rules. Drop the prototype verification at 33 onward. |
| `PRODUCT_DESIGN.md` | Merge → `ARCHITECTURE.md` | Lines 3–6 identify a proposed foundation. Preserve verified knowledge-unit, evidence and version boundaries from 23–71,96–224. Remove superseded tables, product proposals and release gates. |
| `PRODUCT_PLAN_V3_CN.md` | Merge → `CAPTURE.md` | Lines 3–4 identify the v10 baseline. Carry still-true capture and recovery UX from 95–197. Move any distinct preserved data rules to `ARCHITECTURE.md`. Remove the old roadmap at 260 onward. |
| `PRODUCT_RESEARCH.md` | Delete | Lines 3–6 and 63–158 identify a product benchmark and research synthesis. Current outcomes belong in the capture, Ask and design guides. Retain historical research in git. |
| `PROMPT_CACHE_PLAN.md` | Merge → `SETTINGS.md` | Lines 14–53 describe the stable prompt prefix and tail now implemented at `apps/backend/gunther/studio/pipeline.py:205–248,320–357`. Keep the current usage/caching behavior and omit measured-index trials and work instructions. |
| `READ_ALOUD.md` | Merge → `SETTINGS.md` | Lines 6–70 describe narration, speech parts, stored clips and provider jobs. Refresh the API list after the blocking speech route removal. Preserve incremental speech, script, cache use and missing-model error. |
| `REPOSITORY_STRUCTURE.md` | Merge → `ARCHITECTURE.md` | Lines 3–34 map dependency direction and component ownership. Update the map after C using the real Python, TypeScript, Rust and Dart boundaries. |
| `SCIENTIFIC_PRESENTATION_WORKFLOW.md` | Merge → `OUTPUTS.md` | Lines 14–214 describe the study teaching, figure and evidence workflow. Check what ships against `apps/backend/gunther/studio/pipeline.py:515–670,944–1049,1940–2028`. Put still-agreed unbuilt steps in the Facts and Steps plan. Drop handoff/progress/validation sections. |
| `SERVICE_SETTINGS.md` | Merge → `SETTINGS.md` | Lines 12–142 describe provider roles, speech and safe storage. Match them to `apps/backend/gunther/service_settings.py:354–447`, then add shipped usage/cache details. Keep input aliases and legacy saved-settings semantics. |
| `SESSION_WORKSPACE.md` | Merge → `ASK.md` | Lines 21–97 describe the persisted conversation, citations and Library UI. Refresh the actual stream/brief/archive behavior and remove the old blocking/preview path instructions. |
| `STREAMING_DICTATION_PLAN_CN.md` | Merge → `CAPTURE.md` | Lines 3,18–47 distinguish installed field and global shortcut dictation. Carry their stop, copy and saved-audio boundaries from `apps/desktop/src/services/useDictation.ts:98–211` and `apps/desktop/src-tauri/src/dictation_shell.rs:310–344`. Drop implementation and verification logs. |
| `STUDIO_FORMAT.md` | Keep | Lines 3–6 define the writer, reader and export contract. Keep v2 and read-only earlier formats with the surviving parser and check rules. Replace the historical quality-plan link. |
| `TECHNICAL_ARCHITECTURE_V3_CN.md` | Merge → `ARCHITECTURE.md` | Line 4 names schema v10. Carry currently accurate component boundaries from 29–111. Reconcile still-live data, backup and secure pairing contracts into `ARCHITECTURE.md` and `OPERATIONS.md`. Retire old prefixes, design notes and milestones. |
| `TOKEN_USAGE_PLAN.md` | Merge → `SETTINGS.md` | Lines 6–52 describe shipped per-call accounting, storage and Settings → Usage in `apps/backend/gunther/usage_api.py` and `apps/desktop/src/usage/UsageSettings.tsx`. Keep limits, retention and provider-specific omissions proven by tests. |

This new `CLEANUP_PLAN.md` is the execution record until the final docs batch. Retire it with the four cleanup inputs after the final dispositions and batch record are captured outside the repository.

## Merged batch plan

Run batch 00 on the starting HEAD, then phases A → B → E → C → D. A branch that replaces a path sends all current callers to its replacement at cutover and removes the old dispatch. Parity work before cutover stays internal. Changes must preserve old stored artifacts, models/settings, source revisions, recordings, workspace bindings, checkpoints and queues. Keep edge readers and append-only migrations. Verify old fixtures against the delivered path. Keep each batch at about 800 changed lines including moves and tests. Removal-only batches may be larger. Lettered items are separate batches.

**Execution protocol.** During implementation, run the named tests and lint/typecheck for each touched area. Use installed tools and fixture gateways with external calls disabled. Only batch 00 may install the official Flutter SDK. At each batch boundary, stop and write `/tmp/gunther-cleanup/batches/NN.md` with `git diff --stat` output, pass/fail counts, deviations and unresolved points. Include the line `special cases added: none / no-model stand-ins added: none`, or document the exception and why. The supervisor reviews and commits, and handles the push after the phase suite is green. Record a changed assumption in the batch note before changing scope. Use batch 00 failures as the comparison baseline.

**Mobile gate.** Batch 00 installs the SDK from the official Flutter release archive into `/tmp/flutter`. The cached `apps/mobile/.dart_tool/package_config.json` records Flutter 3.47.5 and that SDK location, which `apps/mobile/android/local.properties:1` also uses. Run Flutter analyze and test with the cached dependencies. If installation or the validation commands are blocked, record and skip 08e–h, 10a–c, A49b and the mobile-dependent work in A44b, A45, B46a–b and subsequent shared changes. Keep working mobile routes and formats, including inline upload, v1 live events and the recording-source producer. This also gates removal of the one-shot/unsequenced recording routes. Backend tests can cover shared internals that preserve mobile contracts and receipts.

For tests below, Python files named `test_*.py` are in `apps/backend/tests/` unless given a different path. Desktop tests are relative to `apps/desktop/src/`, Dart tests to `apps/mobile/test/` and native tests to `apps/desktop/src-tauri/`. Use the full phase suites as well as the named batch regressions.

### Phase A — Delete and fix

| Batch | Work | Cap | Tests and gate |
| --- | --- | ---: | --- |
| **00** | On the unchanged starting HEAD, record commit, tool versions, counts and known failures. Install the matching official Flutter SDK in `/tmp/flutter` as above. Repo script entries are at `package.json:19–31` and `apps/desktop/package.json:13–15`. Write `/tmp/gunther-cleanup/batches/00.md` and stop for supervisor approval. | 0 code | Backend `GUNTHER_DESKTOP_SIDECAR=1 apps/backend/.venv/bin/pytest apps/backend` and `apps/backend/.venv/bin/ruff check apps/backend` or installed equivalents. Desktop `npm run typecheck -w @gunther/desktop` and `npm run test -w @gunther/desktop` (Vitest). Rust `cargo test --locked --offline --manifest-path apps/desktop/src-tauri/Cargo.toml`, trying `~/.cargo/bin/cargo` off PATH. Mobile from `apps/mobile` with `/tmp/flutter/bin/flutter analyze --no-pub` and `/tmp/flutter/bin/flutter test --no-pub`. Record missing cached dependencies or platform limits and report any gated work. |
| **01** | Prepare Studio to honor saved/fixed approach and outline, locks, explicit Web choice, style and kind across both entries. Add parity fixtures and checkpoint input validation. | ≤740 | `test_studio_pipeline.py`, `test_studio_science.py`, `test_output_reliability.py`, `outputs/OutputsPage.test.tsx`. Add fixed-plan rebuild cases for reports, general decks and study decks. Check web opt-out, caption/source revision and locked layout. |
| **02** | Switch direct whole build to Studio and remove `SkillAgents.make`, `OutputAgents.build/plan/research` and their private dispatch. Keep remaining work/revise dependencies for their cutovers. | ≤650 | `test_outputs.py`, `test_output_skills.py` surviving scenarios, `test_studio_pipeline.py`. Read an existing saved version and old method settings, and build the next version through the default route. |
| **03** | Make direct Revise use Studio selective part writes. Retire the earlier revise/write path after reference checks. Keep the route used by the artifact eval and the normal rebuild, edit and review controls. | ≤730 | `test_outputs.py`, `test_artifact_steps.py`, `test_output_reliability.py`. Add imported and Studio versions with unchanged sibling bytes and citation maps, explicit retry and parent/replay races. |
| **04** | Implement Studio parity for separate Understand and whole Replan. Use the old work reader for inspection and map saved locks, evidence/source cards and plan identities when the user requests a mutation. | ≤760 | `test_artifact_agent.py`, `test_artifact_workspace.py`, `test_artifact_conversations.py`. Add open-only legacy work and constrained replan cases. |
| **05** | Cut all work operations to the retained Studio part writer. Preserve exact-span edits, structural mutations, restyle, Check and visual review. Stop using the Studio marker to choose a writer. | ≤770 | `test_artifact_steps.py`, `test_studio_redesign.py`, `test_slide_review_api.py`, `test_artifact_conversations.py`. Include scoped rewrite, unchanged text, source/figure locks, failed review and saved resume. |
| **06** | Repoint fixture-driven slide eval imports and validation, and move any surviving read codec. Read old output method settings without using them for runtime selection. | ≤680 | `test_slide_planning.py`, `test_studio_format.py`, `test_developer.py`, `test_service_settings.py`. Add an offline import/argument test for `apps/backend/evals/slides/{plan,over_limit}.py` and a saved-settings fixture. |
| **07** | Delete unreferenced Skills code, earlier-only branches, `StudioSkill`, private prompts/validators and Method controls (D-13, NM-12). Preserve scenario regressions and legacy read fixtures. | Removal-only, ~2,500 | `test_outputs.py`, `test_artifact_conversations.py`, `test_studio_pipeline.py`, `test_studio_science.py`, `outputs/OutputsPage.test.tsx`. Reference-check `SkillAgents`, writer settings and eval imports. Keep installed trace/log/job viewers (D-12). |
| **08a** | Migrate the Ask eval and all blocking fixtures to a test/repo SSE reader with event status checks. Remove the blocking route and desktop wrapper when the call sites are switched. | ≤620 | `test_agent.py`, `test_api.py`, `test_brief.py`, `test_models.py`, `test_developer.py`, `test_usage.py`, `test_backup_tool.py`, `test_library_contents.py`, `test_papers.py`, `test_structured_knowledge.py`, `test_outputs.py`, `test_trash.py`. Add offline eval fixture cases for `done`, `error` and no terminal event. |
| **08b** | Remove automatic Ask skill selection from config, run orchestration and eval options. Keep explicit Compare/Research, budgets and old saved skill attribution. | ≤430 | `test_ask_exam.py`, `test_agent.py`, `test_developer.py`, `pages/SessionWorkspace.test.tsx`, `pages/useHomeAsk.test.ts`. Exercise unadorned Ask, explicit skills, Research stops and a historical auto-skill message. |
| **08c** | Remove `LocalKnowledgeResponder`. Show missing model/key with Settings and model failure with Retry for Home and Library. Save failure/trace consistently, keeping the prior answer library intact (NM-01). | ≤760 | `test_agent.py`, `test_agent_reliability.py`, `test_api.py`, `test_developer.py`, `pages/useHomeAsk.test.ts`, `pages/SessionWorkspace.test.tsx`. Add missing-Ask-role, provider failure, retry on same prompt and old local read tests. |
| **08d** | Remove the blocking Read aloud endpoint and its unused wrapper after checking runtime and device call sites. Preserve saved clips, stream, narration and samples. | ≤420 | `test_tts.py`, `speech/readAloud.test.tsx`, `api.test.ts`. Migrate blocking test fixtures to begin/parts and verify cached and interrupted playback. |
| **08e** | **Flutter gate.** Remove the unused one-shot audio upload and unsequenced append after sequenced recorder/recovery tests pass on both clients. Keep raw old recording and ledger readers. | ≤650 | `test_recording_sessions.py`, `test_recording_duration.py`, `test_storage_budget.py`, `test_request_limits.py`, `services/recordingSpool.native.test.ts`, `knowledge_repository_test.dart`. Convert uploads in fixtures to sequenced chunks and cover replays, corrupt chunk, missing bytes and optional checkpoint fields. |
| **08f** | **Flutter gate.** Move mobile document/photo/share to queued upload receipt and retire inline asset receipt in the same cutover. Preserve source/raw asset IDs, idempotency, legacy formats and retryable parser jobs. | ≤770 | `test_asset_capture.py`, `test_document_pipeline.py`, `test_ocr.py`, `capture/capturePersistence.test.ts`, `knowledge_repository_test.dart`, `capture_outbox_service_test.dart`, `system_share_ingress_service_test.dart`. Test queued receipt losing its connection and reopened stored/outbox captures. |
| **08g–h** | **Flutter gate.** Add test-only mobile v2 event/clock fixtures first. Cut over the mobile live session to v2 within the second batch, then remove the v1 socket/event branches, including any CLI v1 caller. Keep old recorded text and draft state readers. | ≤780 each | First `live_transcription_service_test.dart`, `recording_draft_store_test.dart`, `test_realtime_stream.py`, `test_pause_cuts.py`, `test_listen_again.py` with paired deltas/finals/gaps and 24 kHz input. Then those suites plus `app_test.dart`, `services/liveTranscript.test.ts`, `services/useDictation.test.tsx`. Exercise reconnect offset, Pause/Stop/flush and raw audio saved after network loss. |
| **09** | Remove production sample libraries and offline desktop reply. Keep actual metadata, retry and question draft (D-06, SC-01, NM-02). | ≤650 | `pages/AtlasLibraryPage.test.tsx`, `pages/HomeOpenSession.test.tsx`, `pages/KnowledgeBaseWorkspace.test.tsx`, `pages/SessionWorkspace.test.tsx`. Add offline reconnect draft, missing ID and actual stored ID matching an earlier sample. |
| **10a–c** | **Flutter gate.** Remove unreachable mobile Sources, Overview/Review and Map surfaces separately (M-01/M-02/M-03). Preserve server source/graph endpoints and live capture, counts and navigation. | ~300 / 485 / 310 | Sources `app_test.dart` and `knowledge_repository_test.dart`. Overview/Review `app_test.dart` and `connection_aware_repository_test.dart`. Map `app_test.dart` and `test_api.py`. Each includes Flutter analyze/test and checks remaining repository call sites. |
| **11a–b** | Remove old desktop reader/output controls, drawer and unused wrappers (D-04/D-05/D-11). Remove native orphaned action R-04 separately ahead of the menu move. | ≤550 / ≤120 | Desktop `items/ItemPage.test.tsx`, `items/views/CanonicalReader.test.tsx`, `items/views/DocumentLayoutStatus.test.tsx`, `outputs/OutputsPage.test.tsx`, `pages/KnowledgeBaseWorkspace.test.tsx`, `api.test.ts`, `externalLinks.test.ts`. Native menu tests in `src/capture_shell.rs` and `tests/native_quit_guard.rs`. Re-run reference searches. |
| **32** | Remove the unused source ranker/decoder and tuple extractor (B-08/B-09). | ≤155 | `test_search_meaning.py`, `test_reader_fidelity.py`, `test_asset_capture.py`. Preserve the HTML extraction-limit assertion on the remaining path. |
| **33** | Move demo seed and embedded smoke inputs to repo fixtures. Replace example-dependent authoring prompt text with the general constraints (B-10/B-11, SC-01/02). | ≤540 | `test_api.py`, `test_outputs.py`, `test_studio_format.py`, `test_studio_science.py`. Add clean startup and explicit seed/smoke input tests with no model/network. |
| **A42c** | Remove the opening-prose fallback from both paper details and the canonical draft (NM-05). Leave an absent abstract empty, with opening blocks still readable and searchable. Preserve explicitly marked abstracts and prior saved rows. | ≤740 | `test_papers.py`, `test_structure_draft.py`, `test_search_meaning.py`, `items/views/PaperCard.test.tsx`. Cover an absent abstract, a marked abstract and a quoted opening passage. |
| **A43b** | Remove title word lists (SC-11). Keep structural checks and title locks. Use a reviewer call already made by the path if a semantic check is needed. Where no such call runs, keep the structural checks alone. | ≤780 | `test_studio_format.py`, `test_studio_pipeline.py`, `test_studio_science.py`. Cover short meaningful non-English titles, citations, locked titles and unchanged model-call counts. |
| **A43c** | Keep Ask's first-tool planning fallback and model-written answer. Record the planning failure in trace (NM-06). Show Retry for required grading failures. Preserve scoped raw hits as ungraded when optional grading fails and record the failure in events and trace. Distinguish unavailable/partial audits from empty successful ones and retry unchecked claims while reusing valid coverage (NM-07). | ≤780 | `test_agent_reliability.py`, `test_output_reliability.py`, `test_outputs.py`, `outputs/OutputsPage.test.tsx`. Exercise a planning failure followed by a model answer, required and optional grading failures, and retry with prior verified coverage. |
| **A43d** | Delete post-check numeric citation insertion (NM-11). Preserve existing references and checks. The change requires no new verification or citation-candidate flow. | ≤550 | `test_studio_format.py`, `test_studio_pipeline.py`, `test_output_reliability.py`. Update obsolete borrowing expectations at `apps/backend/tests/test_studio_format.py:160–167,194–225`. Cover a failed check and equal digits with different units, with no reference silently appended. |
| **A44a–b** | Remove the synthetic topic overview generator (NM-04). Separately convert analysis failures to an explicit job status and remove heuristic assertions (NM-03, SC-12). Gate the latter where mobile ingest receipts/behavior depend on it. Keep manual input and original evidence. | ≤690 each | Topic `test_library_contents.py` and a desktop Section Overview regression for prior success, missing key/settings and Retry. Analysis `test_api.py`, `test_structured_knowledge.py`, `test_asset_capture.py`, `test_document_pipeline.py` and gated `knowledge_repository_test.dart`. Verify text remains searchable with zero new guessed assertions, after restart and retry. |
| **A45** | Label transcript time windows accurately and use existing status fields when a failed structure or optional review operation would otherwise only log the failure (NM-08/09). Preserve native text, timed words, saved drafts, geometry and existing error behavior (NM-10). Limit changes to existing status displays and gate any mobile-visible contract change. | ≤750 | `test_structure_model.py`, `test_document_pipeline.py`, `test_retranscribe.py`, `test_paragraphs.py`, `test_slide_review_api.py`, `items/views/DocumentLayoutStatus.test.tsx`, `items/views/TranscribeAgain.test.tsx`. Assert truthful grouping labels, recorded status where supported, retention of the last successful result and no added Retry or incomplete-review controls. |
| **A47a–b** | Fix malformed-outline integrity response (B-13) and standalone JSON fence parsing (B-05) separately. Keep validated v1/v2 seals and one JSON correction turn. | ~160 / 110 | Seal `test_backup_tool.py`, `test_outputs.py`, `test_api.py` with malformed v2 outline HTTP 409 and backup failure. JSON `test_models.py`, `test_agent_reliability.py`, `test_studio_pipeline.py` with inline backticks and exact request count. |
| **A48a–c** | Fix split CRLF in SSE (D-09), decode old incomplete transcript state (D-10), and release voice sample URLs on replacement/failure/unmount (D-14). | ~90 / 160 / 65 | `sse.test.ts`, `api.test.ts` for split CRLF. `services/liveTranscript.test.ts`, `components/LectureRecorder.saving.test.tsx`, `services/recordingSpool.native.test.ts` for older drafts. `speech/readAloud.test.tsx` plus a Settings sample regression for object URL lifetime. |
| **A49a–b** | Fix desktop reading of preexisting trailing recording references (M-04). **Flutter gate** for correcting the mobile producer and mobile-facing index/duration behavior to use one source header. | ≤280 each | `items/sourceContent.test.ts`, `items/ItemPage.test.tsx` for older and new records. Then `app_test.dart`, `recording_draft_store_test.dart`, `test_recording_duration.py` and reader tests for transcripts beyond 2,000 chars, timing and audio links. |
| **A50a–b** | Update the menu-bar state only after the preference is saved (R-05), then verify the parser bundle lock hash before accepting it (S-05). | ~130 / 100 | Rust `src/capture_shell.rs` and desktop `components/AtlasUtilities.test.tsx` with injected storage failure and selector rollback. Parser `tools/document-parser/tests/test_packaging.py` and a fixture that changes only the lock hash. |

### Phase B — Shared building blocks

| Batch | Work | Cap | Tests and gate |
| --- | --- | ---: | --- |
| **12a–b** | Share v1/v2 artifact seal validation with the backup command, then extract verified artifact reads and use one check snapshot (S-01, B-02). Apply A47's fix. | ≤730 each | Seal `test_backup_tool.py`, `test_outputs.py`, `test_api.py` with independent v1/v2 fixtures. Reads `test_artifact_workspace.py`, `test_artifact_conversations.py`, `test_outputs.py` and `test_api.py` for legacy provenance, pinned inputs and normalized check projection. |
| **13a–b** | Give version commit one owner in `artifact_versions.py`. Put the shared artifact route error mapping/header at the boundary (B-01) separately. | ≤780 each | Writer `test_output_reliability.py`, `test_artifact_conversations.py`, `test_artifact_workspace.py` for retry, concurrent writer and guarded save. Errors `test_api.py`, `test_artifact_conversations.py`, `test_slide_review_api.py` with existing 400/404/409/502 detail, pinned hash and access guards. |
| **14a–b** | Share output material and source pool. Take scope/selected units first, then passage windows and figures with the pinned revision reader. | ≤750 each | `test_outputs.py`, `test_artifact_exam.py` and `test_artifact_conversations.py` for scope. `test_studio_science.py`, `test_reader_fidelity.py` for figures, window bounds and source revision. |
| **15a–c** | Share assembly and the remaining outline projection, then factual checks and coverage, then rendered geometry across initial Look and manual Review. | ≤770 each | Assembly `test_outputs.py`, `test_studio_format.py`, `test_studio_pipeline.py`. Checks `test_agent_reliability.py`, `test_output_reliability.py`, `test_outputs.py`. Geometry `test_slide_review.py`, `test_slide_review_api.py`, `outputs/SlideReviewPanel.test.tsx`. Cover byte-stable text and slide/block IDs, no vision and failed fit. |
| **17a–b** | Share the surviving Studio progress/resume stage helper. In the second batch centralize actual provider-attempt admission in `llm.py` (B-12) using A47's fence parsing. | ≤400 / ≤220 | Stages `test_studio_pipeline.py`, `test_artifact_conversations.py`, `outputs/liveOutput.test.ts` for resume and invalid input/citation fingerprint. Admission `test_models.py`, `test_prompt_cache.py`, `test_agent_reliability.py` for correction/retry budgets and zero calls. |
| **18** | Share desktop HTTP transport and typed SSE terminal handling (D-01/D-02), using the split-CRLF fix from A48. Keep 204, workspace binding and channel-specific decoding. | ≤730 | `api.test.ts`, `sse.test.ts`, `pages/liveAnswer.test.ts`, `outputs/liveOutput.test.ts`. Verify new error payloads, normalized status, correct header, abort and follow. |
| **19a–b** | Share scoped passage/section reading, then extract Home and Library listener lifecycle into the same hook. | ≤740 each | Backend `test_agent.py`, `test_ask_exam.py`, `test_search_meaning.py` for web off/on, citation revision and scope. Desktop `pages/useHomeAsk.test.ts`, `pages/SessionWorkspace.test.tsx` for failed/retried turns, selected session and Research stops. |
| **20a–b** | Produce one initial asset parse result by format (SC-14). Put registration/publish rules at the shared worker boundary, preserving leases, staged assets and earlier good heads. If the Flutter gate is closed, leave the inline mobile entry and receipt outside the cutover. | ≤770 each | Parser `test_asset_capture.py`, `test_ocr.py`, `test_documents.py`, `test_document_pipeline.py`. Registration `test_document_pipeline.py`, `test_reader_fidelity.py`, `capture/capturePersistence.test.ts`, `components/BulkImport.test.tsx`. If mobile changes, also `knowledge_repository_test.dart`, `capture_outbox_service_test.dart`, `system_share_ingress_service_test.dart`. |
| **21a–b** | Share fetch/redirect/decode for public reading and snapshot creation. Then reuse desktop capture submission for first save and retry. | ≤670 each | Fetch `test_web_capture.py`, `test_ask_exam.py`. Desktop `capture/capturePersistence.test.ts`, `capture/CaptureWindowApp.test.tsx`. Cover method limits, final provenance, old fingerprints/queued links and wrong-workspace rejection. Verify mobile snapshot/search receipts if affected. |
| **22a–b** | Share recording paths (B-07) around the retained recording ledger and read edges, then share the desktop start/import/restore upload boundary. | ≤740 each | Backend `test_recording_sessions.py`, `test_recording_duration.py`, `test_retranscribe.py`, `test_library_folders.py`, `test_storage_budget.py`, `test_trash.py`. Desktop `components/LectureRecorder.saving.test.tsx`, `services/recordingSpool.test.ts`, `services/recordingSpool.native.test.ts`. Keep incomplete draft and corruption handling. |
| **23a–c** | Share uploaded transcription request/response, then project time-aligned live events once, then share desktop recording/dictation transport. If the Flutter gate is closed, keep v1 adapters and behavior. | ≤760 each | Requests `test_speech.py`, `test_retranscribe.py`. Events `test_realtime_stream.py`, `test_paragraphs.py` and gated `live_transcription_service_test.dart`. Desktop `services/liveTranscript.test.ts`, `services/useDictation.test.tsx`, `dictation/DictationWindowApp.test.tsx`. Preserve gaps, flush, clock offsets and local/missing-provider statuses. |
| **24** | Put desktop draft storage and decoding in one owner (D-07), using the compatible D-10/M-04 readers. | ≤630 | `components/LectureRecorder.test.ts`, `components/LectureRecorder.saving.test.tsx`, `components/AtlasUtilities.test.tsx`, `services/recordingSpool.native.test.ts`, `items/sourceContent.test.ts`. Include a first-app-version spool/draft fixture and a newer checkpoint. |
| **25a–c** | Share owner guards and provider edit/check persistence (B-06), then common key/status controls (D-03/S-09), then the native atomic private-write helper (R-01). | ≤760 each | Backend `test_models.py`, `test_service_settings.py`, `test_speech.py`, `test_tts.py`, `test_developer.py`, `test_mobile_gateway_api.py`. Desktop `components/ServiceSettings.test.tsx`, `models/ModelSettings.test.tsx`, `models/SpeechSettings.test.tsx` for retained/cleared/inherited keys after save failure. Native unit tests for `src/desktop_storage.rs`, `src/storage_paths.rs`, `src/library_location.rs`, `src/capture_shell.rs`. |
| **B46a–b** | Resolve speech features once through the adapter capabilities (SC-13). Move desktop feature switches first, then backend vendor checks. Preserve role, region and credential priority and keep mobile changes under the Flutter gate, including v1-specific handling. | ≤760 each | Desktop `models/SpeechSettings.test.tsx`, `components/LectureRecorder.test.ts`, `services/useDictation.test.tsx`. Backend `test_speech.py`, `test_service_settings.py`, `test_realtime_stream.py`, `test_retranscribe.py`. Cover local and remote providers with equivalent capabilities. |
| **38** | Share script subprocess handling and use the standard file digest (S-02/S-03). Keep argument forwarding and signaled exit semantics. | ≤220 | Add local Node subprocess regressions for success, signal, nonzero and missing executable, plus `tools/document-parser/tests/test_packaging.py` and pure digest fixtures. |
| **39** | Use one parser input manifest in packaging/preflight/CI (S-04). Reuse A50's lock-hash validation (S-05). | ≤240 | `tools/document-parser/tests/test_packaging.py` and a local Node fixture for source changes, missing input, changed lock only and cache invalidation. |

### Phase E — Reading rules

Run this phase after B. For every batch, run the same existing corpus offline on its starting revision and again on the proposed revision. Use the recorded Docling replays (`test_pdf_fidelity.py`, `pdf_replay.py`), the structure fixtures (`apps/backend/tests/layouts/`, `test_structure_draft.py`), and any locally available paper files used by the parsing harness. Include the relevant pipeline and upgrade paths in `test_document_pipeline.py` and `test_docling_upgrade.py`. Record the corpus, commands and revisions in the batch file. Compare the original text, source anchors and reading order, and list every changed block disposition, heading and date field with its before/after values, source evidence and reason it is a fix. Account for every block, including any previously kept content now dropped. Reading changes preserve exact source text and existing editions. If the offline comparison cannot be run, report Phase E as unfinished and the next project, then stop before C and D.

| Batch | Work | Cap | Tests and gate |
| --- | --- | ---: | --- |
| **E41a** | Derive heading levels from explicit structure and document-local layout, with existing titles preserved (SC-03). | ≤780 | Offline corpus plus `test_pdf_fidelity.py`, `test_document_model.py`. Cover unfamiliar headings and documents without headings. |
| **E41b** | Use source evidence for run-ins, front/back matter and retained reading roles (SC-04/05). Read the same revision in the card, viewer and retrieval path, with compatible reads for old editions. | ≤780 | Offline corpus plus `test_document_structure.py`, `test_reader_fidelity.py`, `test_papers.py`, `test_search_meaning.py`. Cover single-page prose headed “Contributions,” an explicit abstract and older revisions. |
| **E41c** | Resolve caption and reference attachment through source links, geometry and ordering (SC-06). Retain text with unresolved ownership. | ≤780 | Offline corpus plus `test_document_model.py`, `test_documents.py`. Cover moved figures and numbered prose alongside captions and reference entries. |
| **E41d** | Derive layout tolerances from the document's page and text geometry (SC-09). | ≤780 | Offline corpus plus `test_document_pipeline.py`, `test_pdf_fidelity.py`. Cover mixed columns and order/placement on varied page sizes. |
| **E41e** | Remove genre-based promotions and drops that lack block-level evidence (SC-17). Keep declared format/profile signals as context. | ≤780 | Offline corpus plus `test_document_model.py`, `test_structure_model.py`. Cover small figures, landscape reports, absent abstracts and a reason for every dropped block. |
| **E42a** | Retain document metadata evidence and disambiguate declared publication, preprint and file dates, publisher/producer, and incompatible identifiers (SC-07). | ≤740 | Offline corpus plus `test_papers.py`, `test_document_structure.py`. Include conflicting dates, different IDs on title matches and old Work/Source links. |
| **E42b** | Use source-backed title provenance and preserve user-owned names (SC-10). | ≤740 | Offline corpus plus `test_papers.py`, `test_digests.py`, `test_asset_capture.py` for provisional titles, explicit titles, typography and user names. |
| **E43a** | Apply observed roles and context bounds to retained text and indexing, and use document frequency for displayed terms (the reading and retrieval part of SC-12). Keep Unicode tokenization and exact text search. | ≤780 | Offline corpus plus `test_search_meaning.py`, `test_library_contents.py`, `test_papers.py`. Verify passage retention, grouping and language-independent term selection. |
| **E43d** | Review the remaining numerical exemptions using field context, units and provenance (SC-16). Keep format validation and the existing verification flow after A43d. | ≤550 | Offline corpus plus `test_studio_format.py`, `test_studio_pipeline.py`, `test_output_reliability.py`. Cover a supported small count, a date used as a claim and identical digits for different units. |

### Phase C — Splits

Re-measure the surviving files after A and B. Split only files still over about 1,000 lines or still mixing unrelated concerns. Recheck eligibility before moving files after E. Move behavior, tests and ownership as a unit, keeping imports and route names stable until their callers move. Keep these batches to moves. For repeated moves, cap each at 780 changed lines with at most 345 existing lines moved per batch before imports/tests.

| Batch | Work | Cap | Tests and gate |
| --- | --- | ---: | --- |
| **26** | Extract the Ask inspector (D-08) and use the existing shared composer/answer projection where possible. Keep trace, citations, sources and Library layout in the installed UI. | ≤740 | `pages/SessionWorkspace.test.tsx`, `pages/HomePage.test.tsx`, `components/evidence/EvidencePanel.test.tsx`, `hooks/useDragWidth.test.tsx`, `test_developer.py`. |
| **27a–f** | Move Studio by concern. Material reads, study map/coverage, scientific review, scientific revision, story/critique, and draft/format each get one batch. Keep stage order and saved keys at the pipeline boundary. | ≤780 each | Material/study/review/revise use `test_studio_science.py` and `test_studio_pipeline.py`. Story/draft use `test_studio_pipeline.py`, `test_studio_format.py`, `test_studio_design.py`. Compare outcomes, snapshots and events. |
| **28a–i** | Move shared evidence types, citations, claim grading, claim lookup and Ask research in five batches. Move Artifacts view, retrieval actions, authoring actions and outcome projection in four. | ≤780 each | Ask batches use `test_agent.py`, `test_agent_reliability.py`, `test_ask_exam.py`. Work batches use `test_artifact_agent.py`, `test_artifact_steps.py`, `test_artifact_conversations.py`. Assert equivalent request boundaries, progress, stop and saved work. |
| **29** | Move remaining service methods by transaction owner. Ask history/brief/turn, proposal/knowledge-unit reads and writes, Artifacts recover/open/turn, and library/source/search helpers. Leave shared service wiring coherent. | Repeated ≤780 | Ask `test_api.py`, `test_brief.py`, `test_ask_exam.py`. Proposals/sources `test_structured_knowledge.py`, `test_library_contents.py`, `test_reader_fidelity.py`, `test_search_meaning.py`. Artifacts `test_artifact_workspace.py`, `test_artifact_conversations.py`, `test_output_reliability.py`. |
| **30** | Move server routers by domain with the guard/fetch work already in place. Dependencies, capture/device, recordings, libraries, Ask and Outputs. Keep mobile route handlers stable when gated. | Repeated ≤780 | Capture/device `test_request_limits.py`, `test_mobile_gateway_api.py`, `test_asset_capture.py`, `test_web_capture.py`. Recordings `test_recording_sessions.py`, `test_realtime_stream.py`. Library/Ask/Outputs `test_api.py`, `test_agent.py`, `test_artifact_conversations.py`, `test_slide_review_api.py`. Diff routes, HTTP detail, stream events and ordering. |
| **31** | Move desktop API groups and the recorder input, transcript and recovery hooks independently. Move Ask history, Capture Sheet view/draft, library editor/Evidence/Account and settings/device components by their existing boundary. | Repeated ≤780 | API `api.test.ts`. Recorder `components/LectureRecorder.permission.test.tsx`, `components/LectureRecorder.saving.test.tsx`. Ask `pages/SessionWorkspace.test.tsx`. Capture/settings `components/AtlasUtilities.test.tsx`, `capture/CaptureWindowApp.test.tsx`, `components/SettingsLayout.test.tsx`. |
| **36a–b** | Move native readiness/nonces and then the process lifecycle into `backend_process.rs` (R-02). | ~395 / 440 | Rust readiness/path/nonces in `src/lib.rs`, `tests/native_quit_guard.rs`, desktop `StartupError.test.tsx`. Retain any stub-child lifecycle case for exit and restart. |
| **37** | Move the existing common native menu frame out of per-state construction (R-03). R-04 is already gone from A11b. | ≤120 | Rust mode/order/action/timer tests in `src/capture_shell.rs`, `tests/native_quit_guard.rs`. Compare all menus before and after. |

### Phase D — Documentation

| Batch | Work | Cap | Tests and gate |
| --- | --- | ---: | --- |
| **40a** | Refresh English `OUTPUTS.md`, `STUDIO_FORMAT.md` and `FACTS_AND_STEPS_PLAN.md`. Fold shipped scientific workflow into `OUTPUTS.md`. Update incoming links and source docstrings when retiring inputs. | ≤780 non-removal changed lines | Check paths, anchors and commands offline. Verify current formats and behavior against `test_outputs.py`, `test_studio_format.py`, `test_studio_pipeline.py` and `test_studio_science.py`. |
| **40b** | Write English `LIBRARY.md`, `ASK.md` and `ARCHITECTURE.md`, including Contents, reading and the repository map. Update `DEVELOPER.md` and `DESIGN_SYSTEM.md`. Repair incoming references before retiring inputs (S-06/S-08). | ≤780 non-removal changed lines | Check paths, anchors and commands offline. Use `test_library_contents.py`, `test_library_folders.py`, `test_document_pipeline.py`, `test_agent.py` and `test_developer.py` to verify the live behavior. |
| **40c** | Write English `CAPTURE.md` for capture, web, recording, live transcripts, dictation, menu bar, summaries and Activity. Link the tray SVG there. Write `SETTINGS.md` for services, models, usage, prompt cache and read aloud. Translate current recovery guidance into `OPERATIONS.md` and refresh root `README.md` as the index. Repair incoming references before retiring inputs (S-07). | ≤780 non-removal changed lines | Check paths, anchors and commands offline. Verify current behavior against `test_activity.py`, `test_web_capture.py`, `test_tts.py`, `test_service_settings.py`, `test_usage.py`, `test_backup_tool.py` and `activity/ActivityStatus.test.tsx`. |
| **40d** | Verify all 52 input verdicts and the target set. Save the accepted batch record externally and retire the four earlier cleanup docs and this plan. | Removal-only | Compare the live document/API references and git file list with the inventory. Check `FACTS_AND_STEPS_PLAN.md` still marks unbuilt work and points at live owners. Check no finished plan dictates a command or quotes obsolete schema or provider behavior. Run available full phase suites and record Flutter status. |

Subdivide doc edits at heading boundaries if needed. Retired-doc deletions can be larger.

**Round 1 disposition.** B-03 and B-04 no longer need separate legacy outline/stage helpers after 02/07. Their retained projections land with 15/17. D-13's UI-only experiment move is superseded by engine retirement. D-12 stays available in the installed app and is covered by 07/26/D40. B-05, B-13, D-09, D-10, D-14, M-04, R-05 and S-05 are Phase A fixes. Their shared pieces and independent small changes carry forward in the tables. M-01–M-04 and the mobile-dependent transport removals wait for the full Flutter gate. Round 1 scenarios remain regression coverage after removal of engine-private assertions.

CLEANUP_PLAN DONE
