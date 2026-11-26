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

The Library workspace uses `--gx-sidebar` for Library, `--gx-canvas` for the middle, and `--gx-surface-2` with `--gx-surface` cards for Inspector. Their 44 px headers share a hairline; Chat uses neutral ink and focus treatments.

Studio document readers use local Source Serif 4 for reading and Inter for controls. The controls honor the Serif and Rounded appearance choices. The title, reading and footer align to a centered column up to 66 characters wide, capped at the content width token. Verified exhibits can use the wider content area, and the map can use the middle pane. Heading size and Edit layout respond to the middle pane's available width. Citation numbers keep a visible keyboard focus indicator. Paper uses a white A4 page, 11 pt reading text and static numbered references. [Studio outputs](OUTPUTS.md#conversations-and-versions)

The Inspector uses underlined tabs and tabular Evidence counts. Evidence opens with context and status, followed by cited sources, key results, or a passage. Supporting provenance uses foldable rows. The Brief uses serif for the goal, selectable text for reading, and inline fields in edit mode. [Conversation Brief](ASK.md#conversation-brief)

Source cards and citation details share the same compact kind mark. Coverage bars and conclusion accents use the existing status colours. The citation header puts the number, source kind, title and locator together. Use passage chips for two to five passages and a select menu for larger lists.

Lucide icons use a neutral stroke and the shared icon sizing. Use a library glyph with its colour and initial to label a library in lists, scope choices and results. Use the brand mark to indicate search or answer activity.

Motion signals a change or work in progress. The fast, regular and slow tokens are 120, 180 and 280 ms. Use the common keyframes for entrances, menu popups, loading and the brand mark. Respect reduced motion.

**Show in answer** scrolls within Chat and fades a 2 px ink outline over 1.2 seconds. With reduced motion, the outline holds for the same duration and clears without animation.

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
