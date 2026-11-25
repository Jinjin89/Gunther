# Right Panel (Inspector → Evidence) & Conversation Brief — Final Proposal

This document is the complete, self-contained design and build specification for redesigning Gunther's Library Inspector **Evidence** tab and **Conversation Brief** (`apps/desktop`, `packages/contracts`, `apps/backend`).

---

## 1. Goals & Design Principles

- **Calm paper-and-ink aesthetic:** Keep Gunther's existing tokens (`apps/desktop/src/design/tokens.css`). White cards (`--gx-surface`) on the neutral inspector canvas (`--gx-surface-2`), ink text (`--gx-ink`), hairline borders (`--gx-line` / `--gx-line-2`), **zero new tinted surfaces**.
- **Colour strictly for identity & status:** Source-kind icons use existing `.gx-kind-icon` tokens (`--gx-id-*`); status uses `--gx-brand` (verified / grounded / new), `--gx-warning` (partial / unchecked / open / issues), `--gx-danger` (disputed / error).
- **Typography:** `--gx-font-display` (serif) for the Brief's Goal and reading quotes; `--gx-font-sans` for controls and structure; `font-variant-numeric: tabular-nums` on every counter, badge number, and fraction.
- **Legible headers everywhere:** Every card title and stage header uses crisp sentence-case ink type — never tiny muted uppercase labels.
- **One path, no special cases (`AGENTS.md`):** One 3-band layout grammar across all five middle states; one edit path for the Brief; one deterministic server-side source of truth for Brief changes; no automatic tab switching.
- **Narrow-panel safe:** Holds cleanly at `336 px` (1280×800) and breathes at `380–440 px` (1728×1000).

---

## 2. Verified Problems Fixed by This Design

Observed in code and confirmed in `/tmp/gunther-panel/shots/01`–`08`:

1. **Brief click-to-input trap & unselectable text (`08-brief-edit-panel-2x.png`):** Every line in `ConversationBrief.tsx` is a `<button>` with `cursor: text`. Drag-selection is blocked, clicking swaps the line into a narrow fixed-width `<input>` that truncates text (`"The pilot recorded roof an"`), drops `[1] [2]` chips, and silently discards changes on `blur`. Adding forces a detached `<select>` + `<input>` footer (`04-brief-add-focused.png`).
2. **Brief buried, double-headed, and hard to scan (`02-brief-open.png`, `08-brief-open-panel-2x.png`):** `<details className="evidence-brief">` sits nested inside `"How it was reached"` (an answer-level section), prints `"Brief"` twice (`.section-label` has zero CSS rules in the app), and renders `Goal / Constraints / Settled / Open` as faint `12 px` `<small>` labels.
3. **Invisible Brief logic & broken cross-turn refs:** `by` (`"sources"` vs `"you"`) and `because` are never shown; `EvidencePane.tsx:115` only passes `selected.citations` to `BriefPanel`, turning `[n]` settled in earlier turns into dead gray `<span>`s even though `session.messages` already holds every earlier citation; nothing shows what changed after the last answer or which answer settled a line.
4. **Flat, noisy Evidence overview (`01`, `06`, `07`):**
   - Cited cards label every library source `"Library"` and print the backend's placeholder fallback `"Source text"` above the quote (`content.py:22`, `document_model.py:985`) instead of the actual source kind icon + meaningful locator.
   - `"How it was reached"` prints raw enum text (`"Source check: complete"` / `"Source check: partial · 1 unchecked"`) instead of a visual coverage meter, and embeds `<ResearchPlan>` which inherits `margin-left: 30px` from `agent.css:322`.
   - `OutputEvidence` places `Research notes` above `Output details`, compresses scope/status/version into one run-on line, and omits source-kind icons.
   - `SourceEvidence` renders bare text buttons with no source context, origin caption, or passage count.
   - `EvidenceSearch` doubles the count (`Also searched, not cited (2) 2 more (0 online)`) and hides stored `query` and `kept`.
   - Empty chat state (`!selected`) is three plain `<p>` tags.
   - Inspector tabs use flat gray fills instead of Gunther's underline-and-count tab language.

---

## 3. Information Architecture (One 3-Band Grammar)

Every Evidence view follows the same vertical rhythm:

1. **Top — Context & Status Card(s):** Compact, scannable summary of what is open (card title `14 px / 600` `--gx-ink`).
2. **Middle — Primary Evidence:** Actionable cards for this state (`Cited` sources, `Key results`, or the reading passage in detail view).
3. **Bottom — Secondary Provenance (folded by default):** Shared `<EvidenceDisclosure>` rows with rotating `ChevronRight` and tabular counts.

| Middle state | Top (always visible) | Middle (primary) | Bottom (folded by default) |
| --- | --- | --- | --- |
| **Chat — answer selected** | **1. Brief card** (promoted out of "How it was reached"; **expanded by default**, collapse persisted globally in `localStorage`).<br>**2. Answer check card:** 4 px segmented Coverage Meter + plain-English summary + `Trace ↗` (when enabled) + folded `Process` disclosure (vertical step timeline + fixed-margin Research Plan). *Omitted when `noSourcesNeeded` and no steps/coverage exist.* | **Cited** (`N`) + `Add all cited (K)` when unimported web citations exist. Numbered cards `[1]`, `[2]`, … with `.gx-kind-icon`, kind + real locator (hiding `"Source text"` fallback), 2-line quote, partial/disputed indicator when flagged, `Show in answer` button, and `WebFindActions` for web hits. | • `Key results used` (`N`)<br>• `Also searched` (`N`) · `K web · M library` (shows `query` and `Filtered` tag when `!hit.kept`)<br>• `Earlier in this conversation` (`N`) |
| **Chat — no answer yet** | **Scope card:** `All N sources` or `K of N selected` + inline kind counts (`3 papers · 2 notes`) + `Web on / off` pill + `Change` link focusing the Library Sources tree + quiet prompt line. | **Brief card** (when `hasBriefToShow` is true, following the same global collapse flag). | *(none)* |
| **Output open** | **Output card:** `Version N` pill + scope label + **Section Coverage Meter** (4 px bar from `artifact.sections`: clean checked / with issues / unchecked + plain count). | **Cited** (`N`) + `Add all cited (K)` + segmented filter (`All` \| `This slide` / `This section` when `> 12` citations & `>= 2` parts). Grouped source cards with `.gx-kind-icon`, tabular `[1] [2]` chips, lead quote, and `WebFindActions`. | • `Research notes` (moved here from top; memo + gaps/limitations)<br>• `Key results used` (`N`)<br>• `Also searched` (`N`) |
| **Source open** | **Source strip:** `.gx-kind-icon` + source title + kind/year/pages metadata + `N key results backed by this source`. | **Key results** (`N`): structured cards with claim text, origin caption (`From chat · …` / `From output · …`), and `K passages →` count pill. Calm explanatory card when `0`. | *(none)* |
| **One citation (detail)** | **Navigation bar (40 px):** `← All N sources`, tabular `1 / 2`, `<` `>` buttons (`[` / `]` shortcuts).<br>**Citation header:** `[n]` badge + `.gx-kind-icon` + title + kind/locator + `Show in answer` button (for chat citations). | Passage chips (`1 · p. 2`, `2 · p. 5`) when `2–5` passages (`<select>` only for `6+`). Passage / Original PDF tabs + reading body + footer (`Open source ↗` or `WebFindActions`). | *(none; `Esc` returns to overview)* |

---

## 4. The Brief as Visible Logic

### 4.1 Card Structure & Default State

- **Standalone top card in Chat Evidence** (shown whenever `hasBriefToShow(brief, updating, hasMessages)` is true). Never nested in `"How it was reached"`, never double-headed.
- **Expanded by default** (verified at both 1728×1000 and 1280×800 in `02-brief-open.png` and `02-brief-open-1280.png` — expanded Brief fits cleanly above Cited sources).
- **Collapse persistence:** One global flag in `persistentStorage` (`gunther:brief-collapsed`, `"1"` / `"0"`, default `"0"` = expanded).
- **Collapsed view:** Card header (`Brief` + updating/error status + `Edit` + expand chevron) + 1-line serif Goal preview (clamped with ellipsis) + compact stage counts (`2 constraints · 3 settled · 2 open`) + change line (when present for the selected answer).
- **Long stage clamping (Read mode only):** When `settled.length > 3` and not expanded, show the **3 newest** items (tail of `settled`, plus any item matching `selected.id` when viewing an older answer) and a quiet `"Show all N"` toggle button (`"Show fewer"` when open). Edit mode always shows all items.

### 4.2 Stage Flow & Readable Headers

All four stages share one visual vertical spine (`1 px solid var(--gx-line-2)` rail on the left of each stage's body) and one crisp header grammar: **sentence-case `13 px`, weight `600`, `--gx-ink`, `14 px` Lucide icon, tabular count in `--gx-muted`**:

1. **Goal** (`Target`, 14 px) — Displayed in **`--gx-font-display` (serif, `15 px`, `line-height: 1.45`, `--gx-ink`)**. Hidden in Read mode when empty; shows `"Set a goal…"` in Edit mode.
2. **Constraints** (`SlidersHorizontal`, 14 px, count) — Scoped rules (`13 px`, `--gx-ink`, `line-height: 1.45`) with subtle bullet markers.
3. **Settled** (`CheckCircle2`, 14 px, count) — Makes `_enforce()` visible on every row:
   - **`by === "sources"`:** `12 px` `--gx-brand` `Check` icon + conclusion text + **clickable tabular `[n]` chips** positioned cleanly inline/wrapped right beneath or trailing without orphan gaps.
     - **Cross-turn ref resolution:** `EvidencePane` builds a conversation citation map `Map<number, ConversationCitation>` across all assistant messages in `session.messages` (selected answer's citations take priority, earlier messages fill any remaining `ref`s). Clicking `[n]` opens that citation in `EvidenceDetail` (passing the owning list and index) even if it was cited in an earlier turn.
   - **`by === "you"`:** `12 px` `--gx-ink` `UserCheck` icon + conclusion text + sub-line in `--gx-muted` (`12 px`): `You agreed: “{because}”` (when `because` is non-empty) or `Added by you` (when empty).
   - **Edited tooltip (no "Pinned" tag):** Any line whose path (`goal`, `constraints:<text>`, `settled:<text>`, `open:<text>`) is in `brief.edited` carries `title="Edited by you — Gunther keeps your wording"` without visual tag noise.
4. **Open** (`CircleDashed`, 14 px, count) — `12 px` `--gx-warning` dashed circle + question text + quiet **`Ask →`** button (`12 px`, `--gx-muted` → `--gx-ink` on hover, always keyboard-reachable) that fills and focuses the chat composer (`chatActions.ask(text, false)` — **never auto-sends**).

### 4.3 Deterministic "What Changed" (Server Source of Truth)

No model prompt changes and no `answers_open` pairing. All diffs are computed deterministically in `apps/backend/gunther/brief.py` inside `BriefKeeper.update()` from `stored.brief` → `merged` and saved in the existing `KnowledgeSession.brief_json` column (zero DB migration; old rows load cleanly with defaults).

**Backend & Contract additions (`brief.py`, `ask_turns.py`, `schemas.py`, `packages/contracts/src/index.ts`):**

```ts
export interface BriefSettled {
  text: string;
  refs: number[];
  by: "you" | "sources";
  because: string;
  /** Assistant message that settled this line (null for older briefs or manual additions). */
  messageId?: string | null;
}

export interface BriefChange {
  messageId: string;
  goalChanged: boolean;
  added: { constraints: string[]; settled: string[]; open: string[] };
  removed: { constraints: string[]; open: string[] };
}

export interface ConversationBrief {
  goal: string;
  constraints: string[];
  settled: BriefSettled[];
  open: string[];
  edited: string[];
  error: string | null;
  throughMessageId?: string | null;
  lastChange?: BriefChange | null;
}
```

**Backend rules (`brief.py` & `ask_turns.py`):**
- `merge()` receives `answered_id: str | None = None`: carried-over settled items (matched via `_same(old.text, fresh.text)`) retain their original `message_id`; newly settled items are stamped `message_id = answered_id`.
- `diff_brief(before: Brief, after: Brief, message_id: str) -> BriefChange | None`: computes `goal_changed` (`not _same(before.goal, after.goal)` when `after.goal` non-empty), `added` (`constraints`, `settled` texts, `open`), and `removed` (`constraints`, `open`) using `_same()`. Returns `None` if nothing changed.
- `Stored` gains `last_change: BriefChange | None = None`, persisted in `brief_json`. `edit_brief()` preserves `before.through`, `before.last_change`, and existing `SettledItem.message_id` (newly user-added settled items get `message_id = None`).
- **No client-side diffing in `useLibraryChats.ts`:** `brief.lastChange` from the API is the single source of truth.

**UI rendering of changes:**
- **When viewing the latest brief-folded answer** (`(!selected || selected.id === brief.throughMessageId)` and `brief.lastChange` is non-null):
  - Show a compact 1-line summary bar at the top of the Brief card:
    `After the last answer: +1 settled · 1 question closed · +1 open` (built dynamically from non-empty parts: `goal updated`, `+N constraints`, `+N settled`, `N question(s) closed`, `+N open`).
  - Newly added lines (and updated Goal) get a `2 px` `--gx-brand` left border and a quiet `New` pill (`11 px`, `--gx-brand` text, `1 px solid color-mix(in srgb, var(--gx-brand) 32%, transparent)`, transparent background).
  - Closed open questions (`removed.open`) appear once under **Open** inside a foldable `<details className="brief-closed" open>` (`Closed (N)`), each rendered muted (`--gx-muted`) with a `12 px` `--gx-brand` `Check` icon.
- **When viewing an older answer** (`selected && brief.throughMessageId && selected.id !== brief.throughMessageId`):
  - Omit the `"After the last answer"` bar, `New` pills, and `Closed` list.
  - Highlight any settled row whose `item.messageId === selected.id` with the `2 px` `--gx-brand` left accent and a quiet `"from this answer"` label (`11 px`, `--gx-muted`).

### 4.4 Read Mode vs Edit Mode (One Edit Path)

- **Read mode (default):**
  - Zero edit chrome — no hover pencils, no `×`, no `<input>`s, no `<select>`.
  - Every line is plain semantic `<p>` / `<span>` text (`user-select: text`), freely selectable and copyable. Clicking text never mutates layout.
  - Only `[n]` citation chips, `Ask →` on Open items, `Show all N`, `Closed` disclosure, and header `Edit` / collapse controls are interactive.
- **Edit mode (toggled by header `Edit` button; `Done` exits):**
  - Clicking `Edit` expands the card if collapsed, shows all four stages (even empty ones), and shows all Settled items (unclamped).
  - Clicking any line activates an inline, full-width, auto-growing `<textarea rows={1}>` (`maxLength={300}`, sized via existing `useAutosize` or scrollHeight sync). On a settled line, its `[n]` chips / `by you` line remain visible (muted) beneath the textarea so context never vanishes while editing.
  - Each row shows trailing icon buttons: `×` (`Remove`) on every row, plus `✓` (`Settle`, moving the item from `open` into `settled` with `by: "you"`, `because: ""` and immediately focusing its textarea so the user can phrase the conclusion) on Open rows.
  - **No `<select>` footer.** Each stage has its own inline button: `"Set a goal…"` (when Goal is empty), `+ Add constraint`, `+ Add conclusion`, `+ Add open question`. Clicking opens a full-width auto-growing `<textarea>` at the bottom of that stage.
  - **No per-line Save/Cancel buttons.** `Enter` or `blur` **saves** (trimmed; clearing an existing line removes it; `edit_brief` collapses line breaks cleanly via `" ".join(text.split())`). `Shift+Enter` inserts a soft newline while drafting. `Escape` reverts the active field and exits it without saving (guard `blur` with a `cancelledRef` flag so reverting does not save). Clicking `×` on the actively edited row uses `onMouseDown={(e) => e.preventDefault()}` so removal does not race `blur`.

---

## 5. Visual Evidence Components & Exact CSS Spec

### 5.1 Shared Primitives (`EvidencePrimitives.tsx`)

1. **`SourceKindMark`:** Compact `22 × 22 px` variant of `.gx-kind-icon` (`border-radius: 6px`, `13 px` Lucide icon) using `kindIcon` (`ContentsRow.tsx:8`) and `--gx-id-*` tokens (`paper`/`file` blue, `note` clay, `link`/web amber with `Globe2`, `image` violet, `table` green, `recording`/`course` rose). Helper `citationMeta(citation, sources)` returns `{ icon, tone, kindLabel, locator }` and suppresses the backend's placeholder `"Source text"` locator (and generic `"public web"` when a hostname exists).
2. **`CoverageMeter`:**
   - Track: `height: 4px`, `border-radius: var(--gx-radius-pill)`, `background: var(--gx-line)`, `display: flex`, `overflow: hidden`, `gap: 1px`.
   - Segment fills: `--gx-brand` (verified / clean checked), `--gx-warning` (partial / unchecked / section issues), `--gx-danger` (disputed / unsupported), `--gx-ghost` (nonfactual).
   - Header line: plain-English summary (`"All 4 claims verified"` or `"3 of 4 claims verified · 1 partial"`; for Outputs: `"5 of 6 sections checked · 1 issue"` or `"All 6 sections checked"`).
   - **Flagged claims list:** Rendered as a collapsible trigger **only** when flagged claims exist (`partly`, `inference`, `disputed`, `not`, `unsourced`, `unchecked`). When all claims are verified, the meter stays a single non-interactive calm line.
3. **`StepTimeline`:** Replaces raw `<AgentSteps>` list inside the folded `Process` disclosure: `1 px solid var(--gx-line-2)` vertical rail at `left: 9px`, `18 × 18 px` circular `--gx-surface` nodes holding `11 px` Lucide icons, 2-line wrapped step label/query, right-aligned `tabular-nums` count (`2`, `0`, or `--gx-danger` error label). Resets `.research-plan` margin inside `.evidence-overview` to `margin: 8px 0 0` (fixing the stray `30 px` indent from `agent.css:322`).
4. **`EvidenceDisclosure`:** Shared `<details className="evidence-disclosure">` with custom summary (`list-style: none`, rotating `13 px` `ChevronRight`, `13 px / 600` `--gx-ink` title, tabular count in `--gx-muted`, optional right-aligned subtitle like `2 web · 2 library`). Used for `Process`, `Research notes`, `Key results used`, `Also searched`, and `Earlier in this conversation`.

### 5.2 Exact Design Tokens & Measurements

| Element | Tokens & Exact Measurements |
| --- | --- |
| **Inspector tabs** (`LibraryInspector.tsx`) | Header `height: 44px`, `padding: 0 8px 0 12px`, `border-bottom: 1px solid var(--gx-line)`. Tab buttons `height: 44px`, `padding: 0 2px`, `gap: 6px`, `font-size: var(--gx-text-sm)` (`13px`), `font-weight: 560`, `color: var(--gx-muted)` → `--gx-ink` active, `2px` bottom bar in `--gx-ink` flush with header border (`transition: transform var(--gx-dur) var(--gx-ease)`). Count pill `min-width: 18px`, `height: 18px`, `padding: 0 5px`, `border-radius: var(--gx-radius-pill)`, `background: var(--gx-hover)` (`--gx-ink` + `--gx-on-ink` when active), `font-size: var(--gx-text-2xs)` (`11px`), `tabular-nums`. Brief-updating dot: `6px` circle in `--gx-brand` with `gx-breathe` on Evidence tab when `briefUpdating`. |
| **Panel container** (`.evidence-overview`) | `padding: 12px 14px 20px`, `display: grid`, `gap: 12px`, `align-content: start`, `background: var(--gx-surface-2)`. |
| **Cards** (Brief, Answer check, Output, Scope, Cited, KeyResult) | `background: var(--gx-surface)`, `border: 1px solid var(--gx-line)`, `border-radius: var(--gx-radius-md)` (`10px`) for top cards, `var(--gx-radius-sm)` (`8px`) for list item cards; `padding: 12px` (top cards), `10px 12px` (item cards). Interactive hover: `border-color: var(--gx-line-2); background: var(--gx-hover)`. |
| **Card titles** (Brief, Answer check, Output, Scope, Cited, Key results) | `font-size: var(--gx-text-md)` (`14px`), `font-weight: 600`, `color: var(--gx-ink)`, `line-height: 1.3`, sentence case, count in `--gx-muted` `tabular-nums`. |
| **Brief stage headers** (`Goal`, `Constraints`, `Settled`, `Open`) | `font-size: var(--gx-text-sm)` (`13px`), `font-weight: 600`, `color: var(--gx-ink)`, sentence case, `14px` Lucide icon (`color: var(--gx-muted)`), count in `--gx-muted` `tabular-nums`, `gap: 6px`. Stage spacing: `12px` between stages, `6px` between header and items. |
| **Brief spine rail** | `margin-left: 6px; padding-left: 12px; border-left: 1px solid var(--gx-line-2)`. New / from-this-answer item accent: `border-left: 2px solid var(--gx-brand); padding-left: 8px; margin-left: -10px`. |
| **Brief text & Goal** | Goal: `font-family: var(--gx-font-display)`, `font-size: calc(15px * var(--gx-fs, 1))`, `line-height: 1.45`, `color: var(--gx-ink)`. Stage rows: `font-size: var(--gx-text-sm)` (`13px`), `line-height: 1.45`, `color: var(--gx-ink)`. Sub-lines (`You agreed`, `from this answer`): `font-size: var(--gx-text-xs)` (`12px`), `color: var(--gx-muted)`. |
| **Brief textarea (Edit mode)** | `width: 100%`, `min-height: 30px`, `padding: 5px 8px`, `border: 1px solid var(--gx-line-3)`, `border-radius: var(--gx-radius-xs)` (`6px`), `background: var(--gx-canvas)`, `color: var(--gx-ink)`, `font: inherit`, `line-height: 1.45`, `resize: none`, `overflow: hidden`. |
| **Citation chips** (`[1]`, `[2]`) | `min-width: 24px`, `height: 20px`, `padding: 0 5px`, `border: 1px solid var(--gx-line-2)`, `border-radius: var(--gx-radius-xs)` (`6px`), `background: var(--gx-surface-2)`, `color: var(--gx-ink)`, `font-size: var(--gx-text-2xs)` (`11px`), `font-weight: 600`, `tabular-nums`. Hover: `background: var(--gx-active); border-color: var(--gx-line-3)`. |
| **Source-kind mark** | `width: 22px; height: 22px; border-radius: 6px; border: 1px solid var(--gx-line); background: var(--gx-surface-2);` icon `13px`, colour from `.gx-kind-icon.kind-*` (Web uses `Globe2` + `--gx-id-amber`). |
| **New / Filtered / Version pills** | `height: 18px; padding: 0 6px; border-radius: var(--gx-radius-pill); font-size: var(--gx-text-2xs); font-weight: 560;` transparent background with `1px solid` hairline (`--gx-brand` for `New`, `--gx-line-2` + `--gx-muted` for `Filtered` / `Version N`). |
| **Show-in-answer pulse** (`.is-located`) | `2px` outline in `--gx-ink` with `outline-offset: 2px; border-radius: 4px;` fading cleanly over `1200ms` (`@keyframes gx-locate`, static `1200ms` border without motion under `prefers-reduced-motion`). |

---

## 6. Keyboard Map

| Context | Key | Action |
| --- | --- | --- |
| **Global workspace** *(existing)* | `Cmd/Ctrl+.` / `Cmd/Ctrl+Shift+E` | Toggle Inspector / switch Evidence ↔ Studio |
| **Citation detail open** *(existing)* | `[` / `]` / `Esc` | Previous citation / next citation / back to Evidence overview |
| **Brief — Read mode** | `Tab` / `Shift+Tab` | Move through collapse toggle, `Edit`, `[n]` citation chips, `Show all N`, `Ask →`, `Closed` disclosure |
| **Brief — Edit mode (no field focused)** | `Esc` | Exit Edit mode (`Done`), does **not** close Inspector (`stopPropagation`) |
| **Brief — Edit mode (line or add textarea focused)** | `Enter` | Save line (trimmed; clearing an existing line removes it) |
| **Brief — Edit mode (line or add textarea focused)** | `Shift+Enter` | Insert newline while drafting |
| **Brief — Edit mode (line or add textarea focused)** | `Esc` | Revert field to pre-edit text and leave field without saving (`stopPropagation`); a second `Esc` exits Edit mode |
| **Evidence disclosures** | `Enter` / `Space` | Expand/collapse disclosure (`Process`, `Research notes`, `Key results used`, `Also searched`, `Earlier`) |

---

## 7. Final ASCII Mockups (~380 px Panel Width)

### 7.1 Chat Answer Selected — Brief Expanded (Read Mode) + Visual Evidence

```
┌──────────────────────────────────────────────┐
│  Evidence [2]    Studio                  [×] │
│  ═════════════                               │
├──────────────────────────────────────────────┤
│ ┌─ Brief ───────────────────── [Edit]  ⌃ ──┐ │
│ │ After the last answer: +1 settled ·      │ │
│ │ 1 question closed · +1 open              │ │
│ │ ──────────────────────────────────────── │ │
│ │ ◎ Goal                                   │ │
│ │ │ Brief the council on the cool roofs    │ │
│ │ │ pilot.                                 │ │
│ │                                          │ │
│ │ ⊟ Constraints  2                         │ │
│ │ │ · Keep the briefing to one page        │ │
│ │ │ · Use measurements from the pilot only │ │
│ │                                          │ │
│ │ ✓ Settled  3                             │ │
│ │ │ ✓ The pilot recorded roof and indoor   │ │
│ │ │   cooling   [1] [2]                    │ │
│ │ │ ◉ Lead with the measured cooling       │ │
│ │ │   You agreed: "Lead with the measured" │ │
│ │ │ ┃ ✓ Coated roof surfaces were 18 °C    │ │
│ │ │ ┃   cooler on clear afternoons  [1] New│ │
│ │                                          │ │
│ │ ○ Open  2                                │ │
│ │ │ ○ How much will a larger rollout       │ │
│ │ │   cost?                         [Ask→] │ │
│ │ │ ┃ ○ Will the cooling persist next      │ │
│ │ │ ┃   summer?                 New [Ask→] │ │
│ │ │ ▾ Closed (1)                           │ │
│ │ │   ✓ When is the next roof inspection?  │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌─ Answer check ─────────────── Trace ↗ ───┐ │
│ │ ✓ All 2 claims verified                  │ │
│ │ ████████████████████████████████████████ │ │
│ │ ▸ Process  2 searches · 2 steps          │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ Cited  2                                     │
│ ┌──────────────────────────────────────────┐ │
│ │ [1] [📝] Roof temperature log        ↖   │ │
│ │          Note                            │ │
│ │          In the cool roofs pilot, the    │ │
│ │          coated roof surface was 18 °C…  │ │
│ └──────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────┐ │
│ │ [2] [📝] Indoor temperature log      ↖   │ │
│ │          Note                            │ │
│ │          In the cool roofs pilot, top-   │ │
│ │          floor offices were 2 °C cooler… │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ▸ Also searched  1          0 web · 1 lib    │
│ ▸ Earlier in this conversation  1            │
└──────────────────────────────────────────────┘
```

### 7.2 Collapsed Brief Card (When User Collapses Brief)

```
┌─ Brief ───────────────────── [Edit]  ⌄ ──┐
│ Brief the council on the cool roofs pil… │
│ 2 constraints · 3 settled · 2 open       │
│ After the last answer: +1 settled · 1    │
│ question closed · +1 open                │
└──────────────────────────────────────────┘
```

### 7.3 Brief Edit Mode (Click Any Line to Edit; Enter/Blur Saves; No `<select>`)

```
┌─ Brief ───────────────────── [Done]  ⌃ ──┐
│ ◎ Goal                                   │
│ │ Brief the council on the cool roofs    │
│ │ pilot.                             [×] │
│                                          │
│ ⊟ Constraints  2                         │
│ │ Keep the briefing to one page      [×] │
│ │ Use measurements from the pilot…   [×] │
│ │ + Add constraint                       │
│                                          │
│ ✓ Settled  3                             │
│ │ ┌────────────────────────────────────┐ │
│ │ │ The pilot recorded roof and indoor │ │
│ │ │ cooling                            │ │
│ │ └────────────────────────────────────┘ │
│ │ [1] [2]  (verified by sources)     [×] │
│ │ Lead with the measured cooling     [×] │
│ │ Coated roof surfaces were 18 °C…   [×] │
│ │ + Add conclusion                       │
│                                          │
│ ○ Open  2                                │
│ │ How much will a larger rollout         │
│ │ cost?                          [✓] [×] │
│ │ Will the cooling persist next          │
│ │ summer?                        [✓] [×] │
│ │ + Add open question                    │
└──────────────────────────────────────────┘
```

### 7.4 Output Open (Same 3-Band Grammar)

```
┌──────────────────────────────────────────────┐
│  Evidence [2]    Studio                  [×] │
│  ═════════════                               │
├──────────────────────────────────────────────┤
│ ┌─ Output ──────────────────── Version 1 ──┐ │
│ │ Scope: the whole library                 │ │
│ │ ✓ All 2 sections checked                 │ │
│ │ ████████████████████████████████████████ │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ Cited  2                                     │
│ ┌──────────────────────────────────────────┐ │
│ │ [📝] Roof temperature log                │ │
│ │      Note                                │ │
│ │      [1]                                 │ │
│ │      In the cool roofs pilot, the coated │ │
│ │      roof surface was 18 °C cooler…      │ │
│ └──────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────┐ │
│ │ [📝] Indoor temperature log              │ │
│ │      Note                                │ │
│ │      [2]                                 │ │
│ │      In the cool roofs pilot, top-floor  │ │
│ │      offices were 2 °C cooler…           │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ▸ Research notes              memo · 1 gap   │
│ ▸ Key results used  1                        │
│ ▸ Also searched  2          0 web · 2 lib    │
└──────────────────────────────────────────────┘
```

---

## 8. Build Plan (4 Reviewable Batches)

Each batch is sized for one focused commit (≤ ~600 changed lines). Front-end Brief pain ships first.

### Batch B1 — Brief Front-End Redesign (Done)

The standalone Brief and its inline editor ship in Evidence. [Conversation Brief](ASK.md#conversation-brief)

### Batch B2 — Brief Changes and Answer Provenance (Done)

The Brief saves answer changes and shows them in Evidence. [Conversation Brief](ASK.md#conversation-brief)

### Batch B3 — Evidence Views and Inspector Tabs (Done)

The Inspector tabs and shared Evidence layout ship for Scope, selected answers, outputs and source key results. [Ask Evidence](ASK.md#evidence-and-checking) · [Library Inspector](LIBRARY.md#library-chat-and-studio) · [Output Evidence](OUTPUTS.md)

### Batch B4 — Citation Detail and Show in Answer (Done)

Citation detail ships with source-kind marks and passage chips. Show in answer appears in the detail header and on numbered Cited cards. [Ask Evidence](ASK.md#evidence-and-checking) · [Design System](DESIGN_SYSTEM.md)

---

## 9. Build schedule (supervisor's split of §8 for parallel builders)

§8's four batches are split so three builders can work at once without editing the same code.

| Batch | Builder | Contents | Starts after |
| --- | --- | --- | --- |
| **B1** | gbuild | §8 B1 exactly (Brief front-end, cross-turn refs, Ask →, edit mode). | Done |
| **B2a** | gbuild2 | §8 B2 backend + contracts only: `SettledItem.message_id`, `BriefChange`, `through_message_id`, `last_change`, `diff_brief()`, `edit_brief()` preserving them; `packages/contracts` types (all new fields optional). No desktop UI. | Done |
| **B3a** | gbuild3 | §8 B3 minus the chat answer view: `EvidencePrimitives.tsx` (+ its own `evidencePrimitives.css`), Inspector tabs, `OutputEvidence`, `SourceEvidence`, `EvidenceSearch`, the no-answer Scope card. Does not touch `ConversationBrief.tsx` or the chat-answer branch of `EvidencePane.tsx`. | Done |
| **B2b** | — | §8 B2 UI wiring: change bar, `New`, `Closed (N)`, "from this answer". | Done |
| **B3b** | — | §8 B3 chat answer view: `Answer check` card (meter + Process timeline), cited cards with `SourceKindMark` + real locator. | Done |
| **B4** | — | §8 B4. | Done |

Shared file rule: B1 and B3a both touch `KnowledgeBaseWorkspace.tsx` (B1: Ask → wiring; B3a: props for
Output/Inspector) — keep those edits small and local. Screenshots go to `/tmp/gunther-panel/shots/<ID>/`.
