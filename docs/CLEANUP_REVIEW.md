# Gunther cleanup review

Reviewed the working tree at `0d66e95`, including in-progress changes. Locations refer to that tree. Evidence comes from source, tests, local history, and isolated checks of pure functions. The execution plan names the tests to run or add when implementing the changes.

## Summary

| Area | Findings | Estimated net lines removed |
| --- | ---: | ---: |
| Backend | 13 | ~234 |
| Desktop | 14 | ~818 |
| Rust | 5 | ~26 |
| Scripts, tools, contracts, and docs | 9 | ~539 |
| Mobile | 4 | ~1,019 |
| **Total** | **45** | **~2,636** |

The estimates count net repository lines, including dead tests. Moves are counted as zero. The findings count includes two owner decisions. The line estimate covers the other 43 findings.

## Base layer proposals

### Backend

- **Artifact HTTP errors (B-01).** Add `@contextmanager def _artifact_http_errors(*, stale: bool = True, bad_input: bool = False, model_failure: bool = False) -> Iterator[None]` in `gunther/api.py`. It translates the repeated synchronous endpoint exceptions with one 404/409 mapping. Opt into the existing 400 and 502 mappings on the routes that already use them. The three artifact reads use `stale=False` to keep their integrity-only 409 rule. Move the existing `WorkspaceHeader` declaration above its first use and reuse it on the two reads that repeat it. **Sites** `apps/backend/gunther/api.py:1508,1532,1558,1850,1873,1897,1915,1939,1963,1985,2010`. **Savings** ~64 lines. **Risk** low, with exact status and detail parity checked per route.
- **Artifact persistence reads (B-02).** Add `gunther/artifact_read.py` with `def artifact_summary(session: Session, artifact: Artifact) -> ArtifactSummaryOut`, `def artifact_detail(session: Session, artifact: Artifact) -> ArtifactOut`, `def latest_check_records(session: Session, artifact_id: str) -> dict[str, dict[str, Any]]`, and `def issues_by_hash(records: Mapping[str, dict[str, Any]]) -> dict[str, list[Issue]]`. Move verification, `ArtifactIntegrityError`, DTO projection and Checker row decoding out of the command service. Both public projections verify the saved row and read the latest check once when they need it. Use the independent seal helper in S-01. **Sites** `apps/backend/gunther/service.py:291,3064–3256,3271–3272,3287,3390,4090,4570–4571,4723,4782,4892–4893,4940–4941,4948,4987,5251–5252,5574,5657–5659`, `apps/backend/gunther/api.py:130`, `apps/backend/tests/test_artifact_workspace.py:821`. **Savings** ~0 net lines, ~190 moved out of `service.py`, and four redundant check reads removed. **Risk** medium. Preserve version-1 and version-2 seal bytes, exact API projections, and the existing snapshot and transaction checks.
- **Output outline entries (B-03).** Extend the serializer in `gunther/slide_planning.py` with `def outline_entry(item: Planned | ItemRec, *, include_identity: bool = True, include_design: bool = True) -> dict[str, Any]`. Use the existing `outline_metadata` for additive identity fields and emit heading, goal, layout, link, design, and lock fields once. Use type-only imports for the two row types. Keep item IDs off report work events and keep the two optional groups off the narrow legacy build event. **Sites** `apps/backend/gunther/outputs.py:610,1605,1668`, `apps/backend/gunther/skill_runner.py:2223`, `apps/backend/gunther/artifact_steps.py:447`. **Savings** ~33 lines. **Risk** medium because the same data is persisted and streamed.
- **Progress events (B-04).** Add `def emit_stage(emit: Events, step: str, label: str, state: str, detail: str | None = None, *, seconds: float | None = None, model_calls: int | None = None) -> None` in `gunther/output_events.py`. Form the common stage payload once, with the Studio clock and call counts still computed by the producer. `AnswerRuns` and `Activity` already share run and activity infrastructure across callers. **Sites** `apps/backend/gunther/skill_runner.py:554`, `apps/backend/gunther/studio/pipeline.py:281,308`, `apps/backend/gunther/slide_review.py:296,437`. **Savings** ~9 lines. **Risk** low. Preserve the running detail updates and include a completed stage's zero call count.
- **Model reply fences (B-05).** Add `def fenced_reply_body(text: str, *, allow_surrounding_text: bool = False) -> str | None` in `gunther/llm.py`. Recognize fence delimiters on their own lines and return the enclosed text verbatim. Writers can keep their existing trimming rules. The gateway can opt into a fence within prose before extracting and validating the JSON object, with its correction turn and request budget intact. **Sites** `apps/backend/gunther/llm.py:538`, `apps/backend/gunther/outputs.py:815`, `apps/backend/gunther/studio/pipeline.py:2309`. **Savings** ~0 lines. **Risk** medium because this changes which imperfect replies are accepted. The bug below gives the reason to consolidate.
- **Desktop settings authorization (B-06).** Add `def require_desktop_owner(request: Request, detail: str) -> None` and `def service_settings(request: Request) -> ServiceSettingsStore` to `gunther/settings_api_common.py`. Replace the identical five authorization predicates and store getters with those calls. Keep each endpoint's current permission and denial detail, including model listing for a paired phone and the stricter sidecar-administrator check in the gateway. **Sites** `apps/backend/gunther/settings_api.py:27,35`, `apps/backend/gunther/models_api.py:38,44`, `apps/backend/gunther/speech_api.py:37,43`, `apps/backend/gunther/tts_api.py:65,71`, `apps/backend/gunther/developer_api.py:29,35`. **Savings** ~26 lines. **Risk** medium due to the authorization boundary.
- **Recording paths (B-07).** Add `def recording_file_paths(root: Path, file_name: str) -> tuple[Path, Path]` in `gunther/recording_metadata.py`. Resolve the existing final name and its appended `.part` companion in one function. The write, export, retranscription and purge callers retain their checks and their own missing-file handling. **Sites** `apps/backend/gunther/recording_service.py:121,338,399`, `apps/backend/gunther/retranscribe.py:779`, `apps/backend/gunther/library_folders.py:450`, `apps/backend/gunther/trash.py:450`. **Savings** ~6 lines. **Risk** low if on-disk names and pre-operation validations are preserved.
- **Model request admission (B-12).** Add `def _admit_model_request() -> None` in `gunther/llm.py`. The gateway applies the current context's budget hook before the initial provider attempt and before each explicit retry. Retain the existing suppression of SDK retries under a budget and the higher-level JSON correction turn. **Sites** `apps/backend/gunther/llm.py:206,321,332,340,345,350,364`. **Savings** ~4 lines. **Risk** medium because each provider attempt must still count exactly once.

### Desktop

- **HTTP request response (D-01).** Add `async function requestResponse(path: string, init?: RequestInit, options?: { expectedWorkspaceId?: string; logErrors?: boolean }): Promise<Response>` in `api.ts`. Reuse `backendFetch` for readiness and credentials, and centralize the common JSON headers, unreachable-service error and non-OK detail. Existing JSON, blob and start-stream callers keep their result decoding and activity notifications. **Sites** `apps/desktop/src/api.ts:523,546,575,605,917`. **Savings** ~52 lines. **Risk** medium. Retain 204 and missing-body semantics, abort identity, status fallbacks, and workspace binding. The upload and recording-chunk helpers keep their specialized responses and retries.
- **Run stream terminal events (D-02).** Add `async function readRunEvents<Result, Event>(body: ReadableStream<Uint8Array>, onEvent: (event: Event) => void, terminal: { stopped: () => Error; failed: string; closed: string }): Promise<Result>` in `sse.ts`. Decode progress and terminal messages once and let callers supply their existing stop errors and fallbacks. **Sites** `apps/desktop/src/api.ts:471,496,511` and their delegates at 542,565,936,947. **Savings** ~12 lines. **Risk** low with tests for stop, error, abrupt close and replay.
- **Secret entry (D-03).** Add `function SecretInput(props: { id: string; label: string; placeholder: string; isSet: boolean; hint?: string | null; describedBy?: string; value: string | undefined; onChange: (value: string | undefined) => void; onCommit?: (value: string) => void }): React.JSX.Element` in `components/SettingsFields.tsx`. Keep reveal, replace, cancel, remove and the undefined/empty sentinel in one component. Callers supply credential origin, placeholder and save policy. **Sites** `apps/desktop/src/components/ServiceSettings.tsx:75,129`, `apps/desktop/src/models/ModelSettings.tsx:53,206`, `apps/desktop/src/models/SpeechSettings.tsx:86,215`, `apps/desktop/src/speech/TtsSettings.tsx:186`. **Savings** ~55 lines. **Risk** medium. Exercise form submission and speech autosave, including inherited keys.
- **Connection badge (D-03).** Add `function ServiceStatusBadge(props: { status: { state: ServiceState; check?: { ok: boolean } | null } }): React.JSX.Element` to the same module. Reuse the identical status-to-label mapping and badge markup across settings families. **Sites** `apps/desktop/src/components/ServiceSettings.tsx:68,340`, `apps/desktop/src/models/ModelSettings.tsx:47,337`, `apps/desktop/src/models/SpeechSettings.tsx:80,344`, `apps/desktop/src/speech/TtsSettings.tsx:301`. **Savings** ~14 lines. **Risk** low.
- **Recording draft storage (D-07).** Add `services/recordingDrafts.ts` with `loadRecordingDrafts(): RecordingDraft[]`, `removeRecordingDraft(id: string): void`, `preserveRecordingDraft(draft: RecordingDraft): void` and `recordingDraftsForWorkspace(drafts: RecordingDraft[], workspaceId: string | null): RecordingDraft[]`. Move the existing types, normalization and storage key beside those functions. The capture sheet imports storage without owning a recorder, and the recorder delegates the draft read/write details. **Sites** `apps/desktop/src/components/LectureRecorder.tsx:70–100,122,237–276,318–353,1142,1372,1848,1943`, `apps/desktop/src/components/AtlasUtilities.tsx:186,207,228,241,356,405`, `apps/desktop/src/components/LectureRecorder.test.ts:2`. **Savings** ~0 net lines and ~110 lines out of the recorder. **Risk** medium. Keep v2/v3 drafts, the workspace guard, and all existing browser and native spool data.
- **Ask context inspector (D-08).** Move `function ContextInspector(props: ContextInspectorProps): React.JSX.Element` into `pages/ContextInspector.tsx`. Move the existing props, citation card and pooled-citation selector with it. The workspace keeps the stream and session state and supplies the inspector's navigation and mutation callbacks. **Sites** `apps/desktop/src/pages/SessionWorkspace.tsx:89,129,393,423,433,1200`. **Savings** ~0 net lines and ~115 lines out of the workspace. **Risk** low. Preserve citation numbers, archived-session affordances and the current resize and focus behavior.

### Rust

- **Private atomic writes (R-01).** Add `pub(crate) fn write_private_atomic(path: &Path, contents: &[u8]) -> std::io::Result<()>` in `src/private_file.rs`. Reuse the existing private-directory and regular-file checks, sync a sibling temporary file, and rename it over the destination. Use distinct staging files for independent writes. Keep serialization and error context at the edges. **Sites** `apps/desktop/src-tauri/src/desktop_storage.rs:26,80–81,113,165,238`, `apps/desktop/src-tauri/src/library_location.rs:43–54`, `apps/desktop/src-tauri/src/storage_paths.rs:24–35`, `apps/desktop/src-tauri/src/capture_shell.rs:883–890`. **Savings** ~4 net lines. **Risk** medium. Preserve filenames, formats, permissions, error details and the checks protecting pending audio and other workspace data.
- **Desktop sidecar lifecycle (R-02).** Add `src/backend_process.rs` with `pub(crate) struct BackendProcess` with a default constructor and private state, `pub(crate) fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>>`, `pub(crate) fn stop_backend(app: &tauri::AppHandle)`, and the existing commands `pub(crate) fn backend_connection(app: tauri::AppHandle) -> Result<BackendConnection, String>` and `pub(crate) fn restart_backend(app: tauri::AppHandle) -> Result<(), String>`. Keep launch, readiness and exit handling in that module. App composition retains lifecycle call sites and command registration. **Sites** `apps/desktop/src-tauri/src/lib.rs:23–60,104–318,405,421,453–531,550,600,623–624,662,669–722`. **Savings** ~0 net lines and ~375 lines out of `lib.rs`, including the tests. **Risk** medium due to process ownership, development mode, quit and library moves.

### Scripts, tools, contracts, and docs

- **Artifact manifest hash (S-01).** Add the stdlib-only `def artifact_manifest_hash(read_field: Callable[[str], Any]) -> str` to `gunther/artifact_manifest.py`. Accept the ORM field getter or SQLite row accessor and keep both seal versions and canonical byte encoding in one function. Callers own row validation and error context. The backup CLI resolves the helper from the checkout so it can still run without importing the web app or ORM. **Sites** `apps/backend/gunther/service.py:225,3173,4051`, `scripts/backup_gunther.py:397–431`. **Savings** ~28 lines. **Risk** medium. Independent legacy test fixtures stay independent and existing archive and artifact hashes must verify byte for byte.
- **Build subprocesses (S-02).** Add `export function runBuildStep(command: string, args: readonly string[], options?: Pick<SpawnSyncOptions, "cwd" | "env">): void` in `scripts/build-step.mjs`, with JSDoc types. Inherit stdio and propagate a child exit code or 1 when killed or unable to spawn. Each command retains its arguments, environment and platform branch. **Sites** `scripts/build-backend-sidecar.mjs:14,22,105,120,139,203,214`, `scripts/build-slide-renderer.mjs:7`, `scripts/prepare-slide-browser.mjs:12`. **Savings** ~12 lines. **Risk** low with child-success, nonzero and signal cases.
- **Parser bundle inputs (S-04, S-05).** Declare one list in `tools/document-parser/manifest-inputs.json` with `{ sources: string[]; lockfile: string; projectFile: string }`, then use `export function parserInputKey(repoRoot: string): string` and `export function parserManifestIsCurrent(repoRoot: string, manifest: { sourceHashes: Record<string, string>; lockHash: string }): boolean` from `scripts/parser-inputs.mjs` for cache key and preflight. Python reads the same manifest inputs to write the existing hash fields. The cache key includes the input spec and the project and lock files. **Sites** `scripts/build-document-parser.py:171–182`, `scripts/build-backend-sidecar.mjs:69–75`, `.github/workflows/quality.yml:107`. **Savings** Three lists become one and the check adds ~20 lines overall. **Risk** medium. The packed parser, on-disk manifest shape, and frozen helper entry point stay compatible.
- **Provider status contract (S-09).** Define `export interface ProviderStatus { state: ServiceState; summary: string; check: (ServiceCheck & { checkedAt: string }) | null }` in `packages/contracts/src/index.ts`. Model, transcription and read-aloud contracts can reference the same field shape. The desktop status component can accept it directly. **Sites** `packages/contracts/src/index.ts:466,539,629`, `apps/desktop/src/models/ModelSettings.tsx:47`, `apps/desktop/src/models/SpeechSettings.tsx:80`, `apps/desktop/src/speech/TtsSettings.tsx:301`. **Savings** ~7 lines. **Risk** low, with the public interfaces and their JSON representations retained.

## Findings

### Backend

#### B-01 · duplicate · low risk · −64 lines

- **Where** `apps/backend/gunther/api.py:1508–1586,1850–2030`
- **What** Eleven artifact endpoints translate the same errors locally. Two repeat the existing workspace header schema.
- **Change** Use the shared context manager and header alias. Keep the render route's query input and the streaming error payloads.
- **Evidence** The identical 409/404 blocks at 1522–1525, 1548–1551 and 1577–1580 are also the core of the blocks at 1904–1907 and 1975–1978.
- **Behaviour change** None, including the extra 400 or 502 only where already mapped.

#### B-02 · missing abstraction · medium risk · ~0 net lines

- **Where** `apps/backend/gunther/service.py:3064–3256,4570–4571,4892–4893,4940–4941`
- **What** Immutable artifact verification and DTO projection occupy ~190 lines of the command service. Four places read the same latest Checker row twice for issues and coverage.
- **Change** Move the read/verification functions to `artifact_read.py` and derive both views from one query.
- **Evidence** `_latest_checks` calls `_latest_check_records` at 3219. `_output_sections`, revise, edit, and check read the records again immediately after calling it.
- **Behaviour change** One consistent Checker snapshot per read. Persisted data, wire JSON and integrity checks keep their existing semantics.

#### B-03 · duplicate · medium risk · −33 lines

- **Where** `apps/backend/gunther/outputs.py:1605,1668`, `apps/backend/gunther/skill_runner.py:2223`, `apps/backend/gunther/artifact_steps.py:447`
- **What** Saved outlines and three live outline producers repeat the field selection and omission rules.
- **Change** Use the outline-entry serializer with the current two optional field groups.
- **Evidence** Compare the mapping unions at 1606–1613 and 2224–2231 with 448–468. `slide_planning.py:60` already serializes the additive identity group.
- **Behaviour change** None to saved JSON or stream events.

#### B-04 · missing abstraction · low risk · −9 lines

- **Where** `apps/backend/gunther/skill_runner.py:554`, `apps/backend/gunther/studio/pipeline.py:281,308`, `apps/backend/gunther/slide_review.py:296,437`
- **What** Five paths construct the same stage payload consumed by the output progress UI.
- **Change** Use `emit_stage` and keep timing, work state and retry decisions in the producers.
- **Evidence** `rg -n '"type": "stage"' apps/backend/gunther -g '*.py'` finds these five constructors. `api.py:1656` consumes their common fields.
- **Behaviour change** None to stage ordering or the SSE contract.

#### B-05 · bug · medium risk · ~0 lines

- **Where** `apps/backend/gunther/llm.py:419,538–545`, `apps/backend/gunther/outputs.py:815`, `apps/backend/gunther/studio/pipeline.py:2309`
- **What** The gateway ends a JSON fence at inline backticks inside a JSON string. The writers have two more versions of the fence regex.
- **Change** Use the shared line-delimited fence reader and retain the existing JSON validation and bounded repair.
- **Evidence** Running the extracted pure parser on a fenced object with three inline backticks in a string produces an unterminated string when decoded.
- **Behaviour change** A valid fenced reply containing inline backticks can pass on its first attempt.

#### B-06 · duplicate · medium risk · −26 lines

- **Where** `apps/backend/gunther/settings_api.py:27`, `models_api.py:38`, `speech_api.py:37`, `tts_api.py:65`, `developer_api.py:29` (backend package)
- **What** Five routers repeat the owner predicate and settings-store access. The denial wording is route-specific.
- **Change** Use the settings helpers at the current call sites, with each caller's message.
- **Evidence** `rg -n '^def (_owner_only|_store)\(' apps/backend/gunther/*api.py` finds those five pairs.
- **Behaviour change** None to desktop permissions, paired-device permissions, settings persistence or status codes.

#### B-07 · missing abstraction · low risk · −6 lines

- **Where** `apps/backend/gunther/recording_service.py:121,338,399`, `retranscribe.py:779`, `library_folders.py:450`, `trash.py:450` (backend package)
- **What** Recording storage, retranscription, folder export and purge repeat the final filename join and three paths append `.part` independently of `RecordingService._paths`.
- **Change** Derive both paths once and preserve each caller's open, reserve, recovery and unlink logic.
- **Evidence** Compare the path pairs at `recording_service.py:124–125,399–400` and `trash.py:450–451`.
- **Behaviour change** None to names, checks, legacy files or recording data.

#### B-08 · dead code · low risk · −83 lines

- **Where** `apps/backend/gunther/service.py:1991–2060`, `apps/backend/gunther/document_model.py:110–122`
- **What** The old raw-source passage ranker and its private splitter are unreachable. The document evidence JSON decoder has no consumers.
- **Change** Delete those definitions. Keep assertion ranking and the manifest-backed library tool.
- **Evidence** `rg -n '\b(_rank_source_passages|_source_text_passages|from_json)\b' apps packages scripts tools docs` finds the two ranker definitions, the ranker's call to its splitter, and the decoder definition.
- **Behaviour change** None. Search, Ask and stored document evidence remain in use.

#### B-09 · dead code · low risk · −9 lines

- **Where** `apps/backend/gunther/asset_service.py:609–617`, `apps/backend/tests/test_asset_capture.py:504–516`
- **What** The tuple-returning `extract_asset_text` shim exists for a single test. Production calls the richer extractor.
- **Change** Remove the shim and keep the HTML character-limit test against `extract_asset_content`.
- **Evidence** `rg -n '\bextract_asset_text\b' apps packages scripts tools docs` finds the definition and test call. Capture calls `extract_asset_content` at 960–966.
- **Behaviour change** None to extraction or downloads.

#### B-10 · special case · low risk · −70 application lines, ~0 repository lines

- **Where** `apps/backend/gunther/service.py:6353–6425`, `apps/backend/gunther/main.py:332–334`, `apps/backend/gunther/config.py:106`
- **What** The app embeds three sample libraries, named example questions, and a sample document whose extracted assertions it marks verified.
- **Change** Move the fixtures and seeding to an explicit development command. Preserve normal startup's unit backfill and keep existing seeded rows.
- **Evidence** `rg -n 'seed_if_empty|Starter knowledge|seed_demo' apps/backend/gunther` ties the fixture to the optional startup flag. `desktop_server.py:231` disables it for desktop.
- **Behaviour change** A development run uses the command to seed examples. Existing workspaces need no conversion.

#### B-11 · special case · low risk · −10 application lines, ~0 repository lines

- **Where** `apps/backend/gunther/studio/smoke.py:11–31`
- **What** The packaged Studio smoke path embeds a regression deck in the application package.
- **Change** Pass the fixture as an explicit input to the internal packaging smoke path and keep the fixture in the repo.
- **Evidence** `FIXTURE` goes straight into `split_slides` and `check_deck` from `smoke.py:29,31`. `desktop_server.py:894` dispatches that entry point.
- **Behaviour change** The private smoke entry point takes input from its caller. The app and existing decks stay compatible.

#### B-12 · duplicate · medium risk · −4 lines

- **Where** `apps/backend/gunther/llm.py:206,321,332,340,345,350,364`
- **What** First send and six provider retries each inline the same request-budget check.
- **Change** Share the admission call. Keep retry decisions, trace messages and transport handling in the gateway.
- **Evidence** `rg -n 'REQUEST_ADMISSION.get' apps/backend/gunther/llm.py` shows those seven checks and the separate SDK retry guard at 491.
- **Behaviour change** None to attempt counts, streaming or limit errors.

#### B-13 · bug · low risk · ~0 lines

- **Where** `apps/backend/gunther/service.py:247,3095–3109,3173–3178`, `apps/backend/gunther/api.py:1577–1580`
- **What** A malformed JSON field in a saved version-2 artifact can escape as a server error during manifest verification.
- **Change** Decode the sealed JSON fields inside the integrity-error boundary. Keep the original row and existing hash algorithm.
- **Evidence** For a valid version-2 row with `outline_json` set to `[`, `json.loads` raises at 248 after the guarded decode completes. The HTTP route maps `ArtifactIntegrityError` to 409.
- **Behaviour change** A damaged artifact returns the established integrity error.

### Desktop

#### D-01 · duplicate · medium risk · −52 lines

- **Where** `apps/desktop/src/api.ts:265,523,546,575,605,917`
- **What** Five transport wrappers repeat connection checks, header setup and fetch error translation. Several await readiness a second time through `backendFetch`.
- **Change** Use the shared response helper while retaining the separate result decoding and specialized recording uploads.
- **Evidence** Compare 527–539, 550–562, 579–599, 609–620 and 921–933. `backendFetch` already waits at 266.
- **Behaviour change** None to credentials, headers, 204 returns, status bodies, notifications or cancellation.

#### D-02 · duplicate · low risk · −12 lines

- **Where** `apps/desktop/src/api.ts:471–480,496–505,511–520`
- **What** Ask, Output and Artifact turn streams repeat the same event loop and JSON parse.
- **Change** Use `readRunEvents` on top of the existing SSE decoder. Pass each flow's messages and stop error.
- **Evidence** All three switch on the same `done`, `stopped` and `error` names and send all other data to `onEvent`.
- **Behaviour change** None to progress order, completed payloads or stop handling.

#### D-03 · duplicate · medium risk · −69 lines

- **Where** `apps/desktop/src/components/ServiceSettings.tsx:68,75`, `models/ModelSettings.tsx:47,53`, `models/SpeechSettings.tsx:80,86`, `speech/TtsSettings.tsx:186,301` (desktop source)
- **What** Three implementations duplicate credential field state and the connection badge. Read aloud already imports both from Transcription.
- **Change** Share the two UI controls. Keep each settings form's policy for when edits are committed.
- **Evidence** `rg -n 'service-secret-mask|service-badge' apps/desktop/src -g '*.tsx'` shows the three implementations. TTS uses the Transcription controls.
- **Behaviour change** None to masked values, unsaved removal, tooltips, inherited credentials or save timing.

#### D-04 · dead code · low risk · −265 lines

- **Where** `apps/desktop/src/components/SourceEvidence.tsx:6`, `WebSnapshotCard.tsx:25`, `StructuredKnowledge.test.tsx:6`, `WebSnapshotCard.test.tsx:3` (same directory)
- **What** The older evidence and snapshot cards are imported only by tests that mount those cards directly.
- **Change** Remove both cards, their direct-render tests and selectors unique to the removed markup. Keep live reader and external-link regressions.
- **Evidence** `rg -n '\b(SourceEvidence|WebSnapshotCard)\b' apps/desktop/src scripts tools packages` shows only definitions and those two tests. Live document views use `EvidencePanel` at `items/views/DocumentView.tsx:298,339`.
- **Behaviour change** None. Preserve stored snapshots and their original-file and source-detail routes.

#### D-05 · dead code · low risk · −94 lines

- **Where** `apps/desktop/src/outputs/ArtifactStudio.tsx:8`, `RevisePanel.tsx:9`, `ScopePicker.tsx:27`, `slides/scene.ts:189` (under outputs)
- **What** The old saved-artifact sidebar, revise form, scope count export and scene verifier have no callers.
- **Change** Drop the two components and two unused exports. Keep live scope and deck validation code.
- **Evidence** `rg -n '\b(ArtifactStudio|RevisePanel|scopeCounts|verifyScene)\b' apps/desktop/src packages scripts tools` finds only definitions. `OutputsPage.tsx:482,490` mounts the current review and conversation controls.
- **Behaviour change** None to Artifacts, revision endpoints, stored outputs or rendering.

#### D-06 · special case · medium risk · −280 lines

- **Where** `apps/desktop/src/atlas.ts:73–381`, `atlasMetadata.ts:5–16`, `App.tsx:123,158,327,568`, `pages/AtlasLibraryPage.tsx:21`, `components/AtlasUtilities.tsx:1147` (desktop source)
- **What** Bundled chapters, named papers, publisher/source rows and sample questions leak into production via three library IDs and an unknown-ID fallback.
- **Change** Use server metadata and Contents for every library. Use the empty or unavailable state for a missing ID. Reuse existing test fixtures and B-10's dev seed for examples.
- **Evidence** `metadataToBase` merges on the literal demo IDs, and `getKnowledgeBase` falls through to sample row zero. Those calls feed the runtime paths at 158,327,568.
- **Behaviour change** Missing libraries show an unavailable state. Existing libraries, sources and IDs in the database remain intact.

#### D-07 · missing abstraction · medium risk · ~0 net lines

- **Where** `apps/desktop/src/components/LectureRecorder.tsx:70–100,122,237–276,318–353`, `apps/desktop/src/components/AtlasUtilities.tsx:186,207,241`
- **What** The capture sheet reads, filters and clears drafts through the component that records audio. Persistence and audio capture have separate lifecycles.
- **Change** Move the draft codec and storage functions to `services/recordingDrafts.ts`. Leave acquisition, uploads and checkpoints with the recorder.
- **Evidence** `loadRecordingDrafts` reads local state at 320 with no recorder instance. The sheet invokes it independently at 207,228,241,356.
- **Behaviour change** None to draft recovery, scope filtering, or on-disk keys.

#### D-08 · missing abstraction · low risk · ~0 net lines

- **Where** `apps/desktop/src/pages/SessionWorkspace.tsx:393–523,1200`
- **What** The Ask workspace contains a separate evidence and scope inspector with a callback-based boundary.
- **Change** Move the inspector, its citation card and its cited-source selector into `pages/ContextInspector.tsx`.
- **Evidence** `rg -n 'ContextInspector|CitationCard|conversationSources' apps/desktop/src` shows the inspector's single mount and its private consumers.
- **Behaviour change** None to Ask, citation navigation, history selection or inspector resize.

#### D-09 · bug · low risk · ~0 lines

- **Where** `apps/desktop/src/sse.ts:14–19`, `apps/desktop/src/sse.test.ts:12–26`
- **What** CRLF normalization on each decoded chunk can miss a line break spanning chunks.
- **Change** Normalize line endings across chunks and add the boundary case to the existing SSE suite.
- **Evidence** The pure reader emits one `done` event for a CRLF-delimited frame in one chunk and zero when the final LF arrives by itself.
- **Behaviour change** A complete run follows its terminal event at this chunk boundary.

#### D-10 · bug · medium risk · ~0 lines

- **Where** `apps/desktop/src/services/liveTranscript.ts:121–143`, `apps/desktop/src/components/LectureRecorder.tsx:326,1674`, `apps/desktop/src/components/TranscriptParagraphs.tsx:64`
- **What** The transcript type guard accepts saved segments that omit grouping state. Readers immediately dereference it.
- **Change** Decode the required grouping state, supplying the idle default for an otherwise readable record with that field absent. Preserve the raw transcript and audio for invalid segments.
- **Evidence** Removing just `grouping` from `emptyTranscript()` still passes `isTranscriptState` and makes `storableTranscript` throw on `grouping.state`.
- **Behaviour change** Incomplete saved segments recover safely. Valid v2/v3 draft data keeps its existing path.

#### D-11 · dead code · low risk · −46 lines

- **Where** `apps/desktop/src/components/AtlasUtilities.tsx:831–857`, `apps/desktop/src/App.tsx:136,504,566,652`, `apps/desktop/src/pages/KnowledgeBaseWorkspace.tsx:24`
- **What** The chapter evidence drawer is mounted with a permanently null chapter.
- **Change** Remove the drawer, unused callback prop and state, plus its exclusive selectors. Keep the evidence panel used by Ask and the document reader.
- **Evidence** `rg -n 'setEvidenceChapter|onEvidence' apps/desktop/src` shows null setters and a prop declaration without a caller of that prop. The drawer returns null at 838.
- **Behaviour change** None to opening a document or citation.

#### D-14 · bug · low risk · ~0 lines

- **Where** `apps/desktop/src/speech/TtsSettings.tsx:14–26,238`
- **What** Replacing a playing voice sample or leaving the settings panel pauses audio and leaves its object URL allocated.
- **Change** Use one sample cleanup path for replacement, end, playback failure and unmount.
- **Evidence** With two consecutive previews and an in-memory audio stub, the first is paused and its URL never revoked. Only the ended callback revokes it.
- **Behaviour change** Interrupted samples release memory while sample playback and Read aloud keep their controls.

### Rust

#### R-01 · duplicate · medium risk · −4 lines

- **Where** `apps/desktop/src-tauri/src/desktop_storage.rs:26–36`, `library_location.rs:43–54`, `storage_paths.rs:24–35`, `capture_shell.rs:883–890` (native source)
- **What** Preference, spool and library-choice writes have separate private-file sequences. Menu-bar settings are written directly.
- **Change** Use the same private atomic write at each current path, with the current JSON encodings.
- **Evidence** `atomic_json` and `save_choice` both open, truncate, write, sync and rename a sibling file. The two other callers write private settings directly.
- **Behaviour change** Interrupted writes keep the last published settings. File formats, location migration and workspace ownership remain compatible.

#### R-02 · missing abstraction · medium risk · ~0 net lines

- **Where** `apps/desktop/src-tauri/src/lib.rs:23–60,104–318,453–531,669–722`
- **What** Sidecar launch, readiness and exit handling share the app builder module with window setup and library migration.
- **Change** Move the sidecar lifecycle and its tests to `backend_process.rs`. Keep setup, quit and library move callers using start/stop and the existing command names.
- **Evidence** `start_backend` is called at 421,528,600 and `stop_backend` at 405,527,662. Readiness and its nonce tests own the token path and parser.
- **Behaviour change** None to the handshake, log routing, dev backend or process exit.

#### R-03 · duplicate · low risk · −12 lines

- **Where** `apps/desktop/src-tauri/src/capture_shell.rs:356–460`
- **What** Every menu layout lists the same Open Gunther row. Five repeat the status row and separator.
- **Change** Assemble that common framing once and keep the state-specific command rows in the match.
- **Evidence** The branches at 379,410,419,432,445 all start with `Status, Separator`, and all six end in `open_main`.
- **Behaviour change** None to order, shortcuts, visible actions or quit guarding.

#### R-04 · dead code · low risk · −10 lines

- **Where** `apps/desktop/src-tauri/src/capture_shell.rs:27,210,468,648–655`
- **What** The native quick-note menu handler has no item that emits its ID.
- **Change** Delete that menu-action branch and keep the app menu's New Note command and note capture.
- **Evidence** `rg -n 'MENU_QUICK_NOTE|QuickNote|"quick-note"' apps/desktop apps/backend scripts tools packages docs` yields only the four sites. New Note is registered separately in `application_menu.rs:60`.
- **Behaviour change** None. Quick-note capture remains available through the live capture entry points.

#### R-05 · bug · low risk · ~0 lines

- **Where** `apps/desktop/src-tauri/src/capture_shell.rs:877–893`, `apps/desktop/src/components/AtlasUtilities.tsx:895–899`
- **What** A failed menu-bar preference save leaves the in-memory setting altered even though the command rejects. The desktop selector is optimistic and keeps the rejected value.
- **Change** Publish the new runtime mode after the file write and restore or refresh the selector on failure.
- **Evidence** When the target preference path is a directory, the write errors after assignment at 882 and before apply at 892. The UI catch only notifies.
- **Behaviour change** The UI and the running tray retain the last saved mode on a failed write.

### Scripts, tools, contracts, and docs

#### S-01 · duplicate · medium risk · −28 lines

- **Where** `apps/backend/gunther/service.py:225–254,3173,4051`, `scripts/backup_gunther.py:397–434`
- **What** Saving, reading and backup verification use the same manifest format. The backup tool copies each seal field and canonicalization rule from the service.
- **Change** Share the stdlib-only hash function using field getters. Assume backup runs from a checkout as documented in `README.md:169`.
- **Evidence** The two implementations branch on provenance version, match on the same fields, and both hash sorted UTF-8 JSON with compact separators.
- **Behaviour change** None to archived bytes, stored versions, integrity checks or CLI arguments.

#### S-02 · duplicate · low risk · −12 lines

- **Where** `scripts/build-backend-sidecar.mjs:14–23,105–185,203–217`, `scripts/build-slide-renderer.mjs:7–8`, `scripts/prepare-slide-browser.mjs:12–15`
- **What** Nine synchronous build steps repeat inherited stdio and child failure handling.
- **Change** Route them through `runBuildStep`. Keep the asynchronous Tauri build and post-build image cleanup in `build-desktop.mjs`.
- **Evidence** `rg -n 'status !== 0' scripts -g '*.mjs'` shows the same exit-code fallback at all nine sites.
- **Behaviour change** None to tool arguments, output, build order, signing or exit codes.

#### S-03 · hand-rolled (use a library) · low risk · −12 lines

- **Where** `scripts/fetch_embedding_model.py:41–46`, `scripts/fetch_vad_model.py:32–37`, `scripts/build-document-parser.py:155–162`
- **What** Whole-file SHA-256 loops repeat across model fetching and parser packaging.
- **Change** Use the standard library's `hashlib.file_digest(fileobj, "sha256").hexdigest()` on the already-open binary files.
- **Evidence** All three loops feed the complete file into `hashlib.sha256`. `README.md:134` and `.python-version:1` specify Python 3.12.
- **Behaviour change** None to checksums, pins, file size verification, or offline packaging.

#### S-04 · missing abstraction · medium risk · +20 lines

- **Where** `scripts/build-document-parser.py:171–182`, `scripts/build-backend-sidecar.mjs:69–75`, `.github/workflows/quality.yml:100–107`
- **What** The packaged parser's source inputs are listed separately for generation, preflight and CI cache reuse.
- **Change** Make all three read the same input list. Leave `tools/document-parser/helper.py:6–10` as the frozen entry point.
- **Evidence** Compare the six `sourceHashes` keys in Python with `sources` in JavaScript and the six source paths in the workflow.
- **Behaviour change** None to manifest consumers or build formats. A source input change invalidates the same checks everywhere.

#### S-05 · bug · low risk · ~0 lines

- **Where** `scripts/build-document-parser.py:171`, `scripts/build-backend-sidecar.mjs:69–75`
- **What** Release preflight validates source hashes but skips the parser lockfile hash stored in the manifest.
- **Change** Compare the declared `lockHash` to the current lockfile before accepting a parser bundle.
- **Evidence** Change only `tools/document-parser/uv.lock` while retaining an existing bundle and the same six sources. The current `sources.some` guard stays false.
- **Behaviour change** Packaging asks for the parser bundle to be regenerated when its dependency lock changes.

#### S-06 · dead code · low risk · −174 lines

- **Where** `docs/OUTPUTS_PLAN.md:3,393–581`, `docs/OUTPUTS.md:229,247,263,342`
- **What** A finished seven-phase plan still tells readers to perform old reviews and repeatedly labels shipped work uncommitted.
- **Change** Replace the obsolete review and progress log with a short historical pointer and carry forward unique compatibility notes and any open verification items.
- **Evidence** The plan's own status says all seven phases were built, and the current Outputs guide records versions, editing, PDF, and streaming.
- **Behaviour change** Documentation only.

#### S-07 · dead code · low risk · −275 lines

- **Where** `docs/ACTIVITY_PLAN.md:173–453,490–495`, `apps/backend/gunther/activity.py:107`, `apps/desktop/src/activity/ActivityStatus.tsx:48`
- **What** The Activity guide still contains backend and desktop implementation instructions for the shipped status surface.
- **Change** Replace the phase tasks with links to the current code and tests. Preserve the event and cancellation contract and the completion record.
- **Evidence** Both implementations exist and the plan's progress log records the finished status pill and stop/revision behavior.
- **Behaviour change** Documentation only.

#### S-08 · dead code · low risk · −51 lines

- **Where** `docs/LIBRARY_CONTENTS_PLAN.md:3,75–80,162–212`, `docs/LIBRARY_FOLDERS.md:53,99,188`
- **What** The completed Contents milestones read as pending instructions. The folder guide still names `Topics/` where the writer publishes `Sections/`.
- **Change** Retire the implementation checklist, keep user-facing rules and migration context, and align the folder guide with the current layout.
- **Evidence** The plan marks L1/L2 built and reviewed. `apps/backend/gunther/library_folders.py:513,539` publishes `Sections/`.
- **Behaviour change** Documentation only. The on-disk layout and migrations stay compatible.

#### S-09 · duplicate · low risk · −7 lines

- **Where** `packages/contracts/src/index.ts:454–471,526–544,616–634`
- **What** Language model, transcription and read-aloud providers duplicate the status shape.
- **Change** Use one structural `ProviderStatus` type and retain each provider's own key origin and model fields.
- **Evidence** `status` at 466,539,629 has identical required fields and nullability.
- **Behaviour change** None to exported provider types' structure or HTTP JSON.

### Mobile

#### M-01 · dead code · low risk · −286 lines

- **Where** `apps/mobile/lib/features/sources/presentation/sources_view.dart:6,63`, `sources_view_model.dart:5` (same directory), `apps/mobile/lib/data/repositories/knowledge_repository.dart:21,123`
- **What** The old Sources tab and its modal composer are unreachable. The composer duplicates the import flow in Capture.
- **Change** Delete the screen and view model, the mobile-only `getSources` forwarding path, and its unused fake method. Keep `SourceSummary`, `SourceDraft` and the server route.
- **Evidence** `rg -n 'SourcesView|SourceComposer|getSources' apps/mobile -g '*.dart'` ends at the orphaned screen, view model, repository and test stub. `app_shell.dart:275–286,359–379` mounts only the three current destinations.
- **Behaviour change** None to mobile import, source data, or `GET /sources`.

#### M-02 · dead code · low risk · −469 lines

- **Where** `apps/mobile/lib/features/overview/presentation/overview_view.dart:7`, `apps/mobile/lib/features/review/presentation/review_view.dart:7`, `apps/mobile/lib/shared/widgets/assertion_statement.dart:4`
- **What** The old Overview and Review screens and their assertion widget have no route.
- **Change** Remove both view/view-model pairs and the widget. Trim `getProvisionalAssertions` and `updateAssertionStatus` from the Dart repositories and fake.
- **Evidence** Neither view has a construction site in `rg -n 'OverviewView|ReviewView|AssertionStatement' apps/mobile -g '*.dart'`. The current Home path still calls `getOverview` at `home_view_model.dart:24`.
- **Behaviour change** None to Home counts, desktop review, assertion data or the HTTP routes.

#### M-03 · dead code · low risk · −294 lines

- **Where** `apps/mobile/lib/features/map/presentation/map_view.dart:8,67`, `map_view_model.dart:5` (same directory), `apps/mobile/lib/data/models/knowledge_graph.dart:50`, `apps/mobile/lib/data/repositories/knowledge_repository.dart:17,85`
- **What** The old graph screen, its painter, and the Dart graph client have no reachable route.
- **Change** Delete that widget tree and its Dart graph-only model and repository methods. Trim the fake graph stub.
- **Evidence** `rg -n 'MapView|KnowledgeGraphPainter|getGraph' apps/mobile -g '*.dart'` traces the only consumer to the orphan screen. `app_shell.dart:275–286` is the root page list.
- **Behaviour change** None to shipped mobile navigation. Preserve `GET /graph` for existing clients.

#### M-04 · bug · medium risk · +30 lines

- **Where** `apps/mobile/lib/features/capture/presentation/capture_view_model.dart:1459–1468`, `apps/desktop/src/items/sourceContent.ts:222–255`, `apps/backend/gunther/recording_metadata.py:19–24`, `apps/backend/gunther/knowledge_index.py:262–276`, `apps/backend/gunther/topics.py:449–460`
- **What** Mobile writes the audio ID after nonempty transcripts. Desktop expects the metadata header and transcript heading. Backend duration paths inspect only the first 2,000 characters.
- **Change** Use the existing recording header and transcript boundary for new mobile saves. Read the prior mobile marker with and without transcript text, including the trailing marker in duration lookup and enqueue.
- **Evidence** The pure desktop parser returns a null recording ID and empty transcript for the mobile format. A 2,400-character transcript followed by the audio ID puts that ID outside the backend's scan.
- **Behaviour change** Mobile captures open with their transcript and audio on desktop and can resolve measured duration. No rows need rewriting and the source API stays compatible.

## Useless features

- **D-12 · useless feature · medium risk** The answer trace inspector and recent logs/jobs viewer are reached through Settings → Developer and Ask → Trace after enabling collection. They account for ~200 desktop lines across `apps/desktop/src/developer/DeveloperSettings.tsx:13–48,86–126`, `AnswerTrace.tsx:1–109`, `useTraces.ts:7–23` and `apps/desktop/src/pages/SessionWorkspace.tsx:495–499`. Move viewing to a repo command if the owner wants to retire the panels. Save ~160 net UI lines after keeping the log-folder shortcut and moving the viewer. Preserve existing traces, file logging, the normal Activity UI and consumers such as `apps/backend/evals/ask/run.py`.
- **D-13 · useless feature · medium risk** The experimental output methods comparison is reachable under Settings → Developer → How outputs are made. `apps/desktop/src/developer/DeveloperSettings.tsx:20–27,50–84` is ~43 lines and calls the existing settings API. Move experiment selection to the repo evaluation command on owner approval and save ~35 net UI lines. Keep the stored flags readable, the core production Studio flow and all legacy output reads.

## Bugs seen along the way

- **B-05** A fenced JSON object with three backticks within a quoted value is truncated inside the string. Running the pure `_json_text` function and then `json.loads` produced an unterminated-string error. `apps/backend/gunther/llm.py:419,538–545`.
- **B-13** A version-2 artifact with an incomplete JSON array in its stored outline passes the initial provenance decode, then raises `JSONDecodeError` from the manifest code. A details request returns 500 through the unhandled exception path instead of the integrity response used for other damaged artifact fields. `apps/backend/gunther/service.py:248,3095–3109,3173`, `apps/backend/gunther/api.py:1577–1580`.
- **D-09** Splitting the final CR and LF of `event: done\r\ndata: {"ok":true}\r\n\r\n` across two chunks yields zero events. The same frame in one chunk yields the expected event. Verified with the pure reader and an in-memory stream. `apps/desktop/src/sse.ts:14–19`.
- **D-10** A stored transcript with all baseline fields and no `grouping` passes the guard, then throws `TypeError` when saved again. Verified with the pure transcript functions. `apps/desktop/src/services/liveTranscript.ts:121–143`.
- **D-14** Starting a second preview before the first playback ends pauses the first player. Its Blob URL is released only by the ended callback, which never runs for the paused sample. Confirmed with the sample function and an in-memory audio stub. `apps/desktop/src/speech/TtsSettings.tsx:14–26,238`.
- **R-05** If a directory exists at the menu-bar preference file path, switching the setting rejects during persistence. The command has already changed its in-memory mode and the UI still shows the rejected mode. Source trace. `apps/desktop/src-tauri/src/capture_shell.rs:877–893`, `apps/desktop/src/components/AtlasUtilities.tsx:895–899`.
- **S-05** With a parser bundle from lockfile A and a checkout at lockfile B, the release preflight succeeds when all six source file hashes are unchanged. The builder already stores the hash of lockfile A. Source trace. `scripts/build-document-parser.py:171`, `scripts/build-backend-sidecar.mjs:69–75`.
- **M-04** A completed mobile recording with transcript text followed by its local recording reference produces no audio URL and an empty transcript in the desktop reader. A transcript longer than 2,000 characters also hides the reference from duration lookup and initial job enqueue. Confirmed with the pure desktop parser and backend reference matching. `apps/mobile/lib/features/capture/presentation/capture_view_model.dart:1459–1468`, `apps/desktop/src/items/sourceContent.ts:222–255`, `apps/desktop/src/items/SourceItem.tsx:152`, `apps/backend/gunther/recording_metadata.py:19–24`, `apps/backend/gunther/knowledge_index.py:265`.

## Execution plan

Changed-line estimates include additions, deletions and moved lines. The tests are implementation gates. The final two batches are owner decisions.

| Batch | Work | Estimated changed lines | Tests |
| --- | --- | ---: | --- |
| 1 | B-01, B-06. Share artifact HTTP errors and desktop settings guards. | 185 | `apps/backend/tests/test_api.py`, `test_artifact_conversations.py`, `test_slide_review_api.py`, `test_models.py`, `test_service_settings.py`, `test_speech.py`, `test_tts.py` and `test_developer.py`. Assert exact 400/404/409/502 detail and paired-device denials. |
| 2 | S-01, B-13. Share the seal function and close the malformed JSON error path. | 165 | `apps/backend/tests/test_backup_tool.py`, `test_api.py` and `test_outputs.py`. Keep the independent v1 fixture. Add a v2 malformed-outline check for HTTP 409 and backup failure. |
| 3 | B-02. Extract artifact reads and use one Checker snapshot per read. | 465 | `apps/backend/tests/test_api.py`, `test_outputs.py`, `test_artifact_conversations.py` and `test_artifact_workspace.py`. Check legacy versions, edited sections and coverage that arrives with the matching issue set. |
| 4 | B-03, B-04, B-12. Consolidate outline fields, stage events and provider-attempt admission. | 225 | `apps/backend/tests/test_artifact_steps.py`, `test_outputs.py`, `test_output_skills.py`, `test_studio_pipeline.py`, `test_slide_review.py`, `test_agent_reliability.py` and `test_prompt_cache.py`. Include omitted fields, zero calls and request counts across retry paths. |
| 5 | B-05. Share fence detection and keep bounded JSON repair. | 95 | `apps/backend/tests/test_models.py`, `test_agent_reliability.py`, `test_studio_pipeline.py` and `test_outputs.py`. Add a fenced JSON string with inline backticks and verify the one-attempt result. |
| 6 | B-07. Reuse final and partial recording paths. | 75 | `apps/backend/tests/test_recording_sessions.py`, `test_recording_duration.py`, `test_retranscribe.py`, `test_library_folders.py`, `test_storage_budget.py` and `test_trash.py`. |
| 7 | D-01, D-02, D-09. Share desktop transport and terminal decoding, then handle split CRLF. | 205 | `apps/desktop/src/api.test.ts`, `sse.test.ts`, `pages/liveAnswer.test.ts` and `outputs/liveOutput.test.ts`. Add the CRLF split test and assert 204, error, abort and workspace header behavior. |
| 8 | S-09, D-03. Share provider status types and settings controls. | 185 | Desktop typecheck and `apps/desktop/src/components/ServiceSettings.test.tsx`, `models/ModelSettings.test.tsx`, `models/SpeechSettings.test.tsx` and `speech/readAloud.test.tsx`. Exercise replace, cancel, remove, inherited keys and form or autosave timing. |
| 9 | D-07, D-10. Extract draft storage and decode older or incomplete segments safely. | 285 | `apps/desktop/src/components/LectureRecorder.test.ts`, `LectureRecorder.saving.test.tsx`, `AtlasUtilities.test.tsx`, `apps/desktop/src/services/liveTranscript.test.ts` and `recordingSpool.native.test.ts`. Add a missing-grouping draft that still plays and saves its text. |
| 10 | D-08. Move the Ask inspector and its citation projection. | 265 | `apps/desktop/src/pages/SessionWorkspace.test.tsx`, `ConversationBrief.test.tsx`, `apps/desktop/src/components/evidence/EvidencePanel.test.tsx` and `apps/desktop/src/hooks/useDragWidth.test.tsx`. Exercise citations and archived-session source controls. |
| 11 | R-01, R-05. Use private atomic writes and publish menu preference after save. | 150 | Rust unit tests in `desktop_storage.rs`, `storage_paths.rs`, `library_location.rs` and `capture_shell.rs`. Add a failing preference writer test and a selector rollback test in `apps/desktop/src/components/AtlasUtilities.test.tsx`. |
| 12 | R-02, part 1. Extract readiness commands, token parsing and their tests. | 395 | Rust tests `ready_token_is_bound_to_the_expected_nonce`, `every_nonce_has_an_isolated_ready_path` and `launch_nonces_are_unique_lowercase_hex` from `apps/desktop/src-tauri/src/lib.rs`. |
| 13 | R-02, part 2. Move sidecar state and spawn/watch/stop code. | 440 | The readiness tests, `apps/desktop/src-tauri/tests/native_quit_guard.rs` and `apps/desktop/src/StartupError.test.tsx`. Add a native lifecycle test with a stub child for exit before readiness and restart. |
| 14 | S-02, S-03. Consolidate build subprocess handling and use the standard file digest. | 140 | Add `node --test` cases for successful, missing, signaled and nonzero child commands. Run `tools/document-parser/tests/test_packaging.py` and compare whole-file digests on local fixture bytes. |
| 15 | S-04, S-05. Share parser inputs and check the lock hash. | 160 | `tools/document-parser/tests/test_packaging.py`. Add `scripts/parser-inputs.test.mjs` with current, missing-source and lockfile-only changes against a synthetic manifest. Check the cache key changes for a new input. |
| 16 | M-04. Publish one recording header and accept earlier mobile text. | 145 | `apps/mobile/test/app_test.dart`, `apps/desktop/src/items/sourceContent.test.ts`, `apps/desktop/src/items/ItemPage.test.tsx` and `apps/backend/tests/test_recording_duration.py`. Cover new saves, legacy mobile text, audio playback links and a transcript over 2,000 characters. |
| 17 | D-14. Release replaced or interrupted voice sample URLs. | 65 | Extend `apps/desktop/src/speech/readAloud.test.tsx` with sample replacement, unmount and play rejection. |
| 18 | B-08, B-09, B-10, B-11. Remove unreachable helpers and move bundled examples to repo commands. | 355 | `apps/backend/tests/test_agent.py`, `test_search_meaning.py`, `test_reader_fidelity.py`, `test_asset_capture.py` and `test_outputs.py`. Add empty-startup and explicit development seed checks. Keep an explicit fixture-based Studio smoke and `test_studio_format.py`. |
| 19 | D-04, D-05, D-11. Remove orphaned reader cards, output controls and the old drawer. | 435 | `apps/desktop/src/items/ItemPage.test.tsx`, `items/views/CanonicalReader.test.tsx`, `items/views/DocumentLayoutStatus.test.tsx`, `outputs/OutputsPage.test.tsx`, `pages/KnowledgeBaseWorkspace.test.tsx` and `externalLinks.test.ts`. Confirm removals with the same reference searches. |
| 20 | D-06. Replace bundled sample libraries with service-backed states and development fixtures. | 370 | `apps/desktop/src/pages/AtlasLibraryPage.test.tsx`, `HomeOpenSession.test.tsx`, `KnowledgeBaseWorkspace.test.tsx` and `SessionWorkspace.test.tsx`. Add a stale-library-ID regression and exercise a real library whose title or ID matches an old sample. |
| 21 | R-03, R-04. Simplify native menu rows and remove the orphan action. | 90 | Capture-shell layout tests in `apps/desktop/src-tauri/src/capture_shell.rs` and `apps/desktop/src-tauri/tests/native_quit_guard.rs`. Add a menu order assertion if needed. |
| 22 | M-01. Remove the unreachable Sources screen. | 300 | `apps/mobile/test/app_test.dart` and `knowledge_repository_test.dart`. Keep source imports, notes, capture and the three navigation destinations covered. |
| 23 | M-02. Remove the unreachable Overview and Review screens. | 485 | `apps/mobile/test/app_test.dart`, `connection_aware_repository_test.dart` and `apps/backend/tests/test_api.py`. Keep Home counts and legacy assertion endpoints covered. |
| 24 | M-03. Remove the unreachable Map screen and its private Dart graph client. | 310 | `apps/mobile/test/app_test.dart` and `apps/backend/tests/test_api.py`. Keep navigation and the existing server graph contract covered. |
| 25 | S-06, S-08. Retire the old Outputs and Contents implementation instructions and update the folder description. | 270 | Verify document links and reconcile the retained claims with `apps/backend/tests/test_outputs.py`, `test_library_contents.py` and `test_library_folders.py`. |
| 26 | S-07. Retire the shipped Activity build checklist. | 300 | Verify links and contract details against `apps/backend/tests/test_activity.py` and `apps/desktop/src/activity/ActivityStatus.test.tsx`. |
| 27 | D-12, if chosen. Move diagnostic viewing out of Settings and Ask. | 275 | `apps/backend/tests/test_developer.py`, `apps/desktop/src/pages/SessionWorkspace.test.tsx` and `apps/desktop/src/activity/ActivityStatus.test.tsx`. Add a command test for viewing saved traces and recent jobs. |
| 28 | D-13, if chosen. Move output method comparison to the repo eval runner. | 115 | `apps/backend/tests/test_studio_pipeline.py` and `test_output_skills.py`. Add an offline eval argument test and check old settings still load. |

CLEANUP_REVIEW DONE
