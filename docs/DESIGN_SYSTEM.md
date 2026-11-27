# Gunther Design System

Use neutral page surfaces and ink for actions. Green marks Gunther and grounded or saved work. Library identity, source kinds and status use small colour accents. Use a serif for titles and reading, and system sans for controls.

## Desktop styles

| Files under [design](../apps/desktop/src/design/) | Own |
| --- | --- |
| `tokens.css` | Colours, typography, radius, motion and light/dark tokens |
| `primitives.css` | Buttons, tabs, icons, menus, banners, loading and empty states |
| `shell.css`, `home.css`, `pages.css` | Navigation, the Home composer and library and Inbox pages |
| `legacy.css` | Shared surface rules for Ask, Notebook, Settings and Account |
| `prose.css`, `item.css`, `capture.css` | Reading and editing, detail pages and capture surfaces |
| `shortcuts.css`, `BrandMark.tsx`, `LibraryGlyph.tsx` | Shortcut sheet, brand mark and library identity |

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--gx-canvas` | `#ffffff` | `#1f1f1d` | Page |
| `--gx-sidebar` | `#f9f9f7` | `#1a1a18` | Navigation |
| `--gx-surface` | `#ffffff` | `#262624` | Cards and panels |
| `--gx-surface-2` | `#fafaf8` | `#2a2a27` | Secondary surfaces, including the Library Inspector |
| `--gx-ink` | `#1a1a18` | `#f3f2ee` | Text and primary actions |
| `--gx-muted` / `--gx-faint` | `#5f5e5a` / `#6b6a65` | `#b9b7af` / `#a3a19a` | Secondary text |
| `--gx-brand` | `#16745c` | `#5fbf9f` | Brand mark, saved status and focus |

Choose tokens from the CSS when adding surfaces. Use `--gx-hover` and `--gx-active` for hover and selection, `--gx-ghost` for placeholders or disabled states, and `--gx-danger` for errors and destructive actions. Apply the selected Light, Dark or Match system appearance before paint. Use tabular figures for counters and times.

The Library workspace uses `--gx-sidebar` for Library, `--gx-canvas` for the middle, and `--gx-surface-2` with `--gx-surface` cards for Inspector. Their 44px headers share a hairline. Chat uses neutral ink and focus treatments.

Studio rows use a 16 px catalog icon, a 14 px semibold title and 12 px details. Titles and details wrap as the Inspector resizes. The current row has a 2 px ink rule. Row menus appear on hover or keyboard focus and stay visible on touch. [Studio outputs](OUTPUTS.md#conversations-and-versions)

The Inspector uses underlined tabs and tabular Evidence counts. Evidence opens with context and status, followed by cited sources, key results, or a passage. Supporting provenance uses foldable rows. The Brief uses serif for the goal, selectable text for reading, and inline fields in edit mode. [Conversation Brief](ASK.md#conversation-brief)

Source cards and citation details share the same compact kind mark. Coverage bars and conclusion accents use the existing status colours. The citation header puts the number, source kind, title and locator together. Use passage chips for two to five passages and a select menu for larger lists.

Lucide icons use a neutral stroke and the shared icon sizing. Use a library glyph with its colour and initial to label a library in lists, scope choices and results. Use the brand mark to indicate search or answer activity.

Motion signals a change or work in progress. The fast, regular and slow tokens are 120, 180 and 280 ms. Use the common keyframes for entrances, menu popups, loading and the brand mark. Respect reduced motion.

**Show in answer** scrolls within Chat and fades a 2 px ink outline over 1.2 seconds. With reduced motion, the outline holds for the same duration and clears without animation.

## Library workspace widths

The three columns are Library, middle and Inspector. Measure `W` from `.library-workspace` after app navigation and `L` from the Library track's outer width including its border. Observe both so a dock drag recalculates capacity even when the window stays the same. The Library dock is 200–360px, with a 260px default and 44px rail. The live Inspector calculation uses CSS pixels.

```text
R        = W - L
Imax     = min(960, floor(R / 2), floor(R - 480))
Idefault = clamp(336, round(R / 3), 400)
I        = clamp(280, chosenInspectorWidth ?? Idefault, Imax)  when Imax >= 280
M        = W - L - effectiveInspectorTrack
```

The sizing rule reserves 480px for the middle and gives the Inspector at most half the space left by the Library. Studio, Evidence Passage, images and Original PDF use the same dock bounds. Original uses the full Inspector panel and its measured Fit. A capacity clamp preserves the stored choice. Dock grips support 24px keyboard steps and double-click reset to the live default.

Viewport collapse and overlay rules use 840px for Library, 1080px for Inspector and the 1200px Library rail preference for wide readers. Workspace capacity can force a rail sooner. At `W >= 568px`, a Library that cannot fit beside a 480px middle and the right rail uses the 44px track. An Inspector that cannot dock uses a 44px rail and opens as an overlay. At `W < 568px`, a single 44px **Workspace panels** strip above Chat, Source, Setup and Output holds the Library, Evidence and Studio controls. The side tracks become zero and overlays open below the strip within the workspace. One overlay opens at a time. Escape backs out of Evidence detail before closing an overlay and focus returns to its trigger. [Workspace columns](../apps/desktop/src/pages/KnowledgeBaseWorkspace.tsx) · [Inspector and Studio styles](../apps/desktop/src/pages/libraryWorkspace.css)

Studio centers its sections at up to `45rem`. The tile grid uses `repeat(auto-fit, minmax(min(100%, 7.5rem), 1fr))` with an 8px gap. The output card grid uses `repeat(auto-fill, minmax(min(100%, 17rem), 1fr))` and retains newest-first order. Evidence passages and their context use a centered measure of up to `66ch`. [Evidence styles](../apps/desktop/src/design/evidence.css)

## Output reader widths

`.library-middle-page` owns scrolling and the `library-reader` inline-size container. The `article.output-reader` uses the middle pane's full width and an inline gutter of `clamp(16px, 2.5cqi, 32px)`. Each `.reader-track` is centered and capped by the available width inside that gutter. `data-kind` identifies the output shape, and `data-mode` identifies read, edit or plan. Reading uses local Source Serif 4 and controls use Inter with the appearance setting's overrides. Citation numbers retain a visible keyboard focus indicator.

| `data-track` | Width and behavior |
| --- | --- |
| `prose` | Up to `min(66ch, 760px)` for narrative and conversation controls. |
| `structured` | Up to 960px for flashcard and quiz surfaces, audio transport and voices. Prompts, explanations and transcript text use up to `66ch`. |
| `report` | Up to 1280px for the report and exhibits. Headings, navigation and body text align to a nested prose column. Markdown wrappers in the preamble and sections, including the summary section, span the full grid. Their `.rp-table` children and structured `.rp-exhibit` blocks can use that width. Dense tables and display math scroll locally. |
| `deck` | Up to `max(480px, (100vh - 240px) × 16/9)` for the 16:9 stage, toolbar, thumbnail strip and notes shell. Reveal uses 1280×720 internally. Notes text and fit notices cap at `66ch`. Present opens in a full-window layer. |
| `review` | Up to 1320px with 20px inline insets. Full-size slide renders scroll inside the preview. |
| `plan` | Up to 1200px. Above `64em` of reader width the review and settings use `2fr` and `1fr` tracks with a 24px gap. The layout is one column at `64em` and below. |
| `full` | Uses the available inner width for the mind map and Edit. All Edit readers use two equal columns with a 24px gap and stack at `@container library-reader (max-width: 50em)`. |

While you review a plan, its action bar sticks to the bottom of the middle pane's scroller. Its buttons wrap, and scroll padding keeps the feedback form and history reachable. Existing preference rows use textareas that wrap and grow with their content as the settings column narrows. [Planning styles](../apps/desktop/src/outputs/planning.css)

Print uses a dedicated `#print-root`. A4 output prepares at 178mm with static numbered citations. Slide print uses the native 1280×720 stage. The screen tracks and container rules stay outside these print measurements. [Reader styles](../apps/desktop/src/outputs/outputs.css) · [Report styles](../apps/desktop/src/outputs/deck/report.css) · [Studio exports](STUDIO_FORMAT.md#6-exports-desktop)

## Panels and reading

- Use shared ink, quiet and ghost buttons. Icon buttons carry an accessible name. Name the action where the user needs to decide.
- Home starts with search. `@` opens the library list, and selecting a library adds a removable scope chip. Arrow keys and Enter move and open results. Use the labelled listbox and active descendant for the scope menu.
- Library Contents shows sections and source rows. A source opens in its own detail page from Contents, search or Inbox. The detail toolbar handles navigation and Trash. Use the stable source identity when opening or updating a row.
- Source pages give the original and its reading a single home. Use the matching reader for notes, recordings, captured web pages, documents, photos and tables. Display reading status, evidence and filing options beside the content where available.
- Markdown uses React Markdown and GFM. Render text and trusted local evidence through their readers, and use links for remote images. CodeMirror provides the Markdown editing commands. The saved content is portable Markdown.
- Code blocks use a mono font, a language label and a Copy action. Syntax colours follow the app theme. Closed blocks over 25 lines show the first 20 with a **Show all** control. Print shows every line with the light palette.
- Picture cards use a white canvas and preserve the drawing's intrinsic size as a maximum. Mermaid uses the neutral palette in both themes. Keep Picture and Code visible, and reveal Copy, Save and Expand on hover or keyboard focus.
- Moving to Trash offers Undo. Restore is available from Trash. Delete forever and Empty Trash require inline confirmation. [Library and Trash](LIBRARY.md#trash)

## Keyboard

The [shortcut registry](../apps/desktop/src/shortcuts/shortcuts.ts) supplies matching and labels. The desktop shell handles native menu shortcuts once. Single-letter shortcuts pause in editable fields, and Esc dismisses the topmost layer first. Use Cmd on macOS and Ctrl for the platform modifier elsewhere.

| macOS keys | Action |
| --- | --- |
| Cmd+K or / | Search |
| Cmd+, and Cmd+/ | Settings and the shortcut sheet |
| Cmd+1 / Cmd+2 / Cmd+3 | Home / Libraries / Inbox |
| Cmd+Shift+C / Cmd+Shift+R / Cmd+N | Capture / new recording / new note |
| Cmd+1–6 in Capture, Cmd+Return | Switch capture kind, save |
| J / K, arrows and Return | Navigate or open an item |
| E and Cmd+E | Edit a note and switch writing or preview |
| Space and Left / Right | Play or pause a recording and seek by five seconds |
| Cmd+Backspace, Cmd+Z | Move an item to Trash and undo |
| Cmd+Shift+M and macOS Option+Space | Ask dictation and global dictation |

The tray icon and menu follow capture state. [Capture and menu bar](CAPTURE.md#dictation-and-menu-bar)

## Accessibility

Keep the workspace's main, navigation and banner landmarks. Label dialogs and input controls. Announce changing counts and status, give controls a visible focus state, and keep reading contrast in both themes. Use the reduced-motion setting for every surface. Desktop pages use shared test fixtures to exercise their accessible names and controls.
