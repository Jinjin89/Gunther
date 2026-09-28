# Gunther Design System

White paper, black ink, one brand green. Gunther is a place to think, so the
interface stays quiet: colour is reserved for identity and status, and motion
only ever explains a change.

The system lives in `apps/desktop/src/design/` and loads after every older
stylesheet, so its tokens win everywhere.

| File | Owns |
| --- | --- |
| `tokens.css` | Colour, type, radius, elevation and motion tokens (light + dark); maps every legacy `--atlas-*` / `--accent` variable onto them; shared keyframes; reduced-motion rule |
| `primitives.css` | Buttons, icon buttons, links, `kbd`, library glyphs, brand mark, chips, kind icons, underline tabs, menus, banners, spinners, skeletons, empty states |
| `shell.css` | Titlebar, sidebar, page frame and page header |
| `home.css` | Search-first Home: greeting, composer, `@` menu, capture chips, "jump back in", results |
| `pages.css` | Libraries and Inbox |
| `legacy.css` | Brings the library workspace, Ask, Notebook, Capture, Settings and Account onto the system |
| `BrandMark.tsx` | The single-stroke "G" whose crossbar ends in a knowledge node |
| `LibraryGlyph.tsx` | A library's identity: its colour and first letter |

## Principles

1. **Search first.** Home is a composer, not a dashboard. Everything else is one
   keystroke away (`⌘K` or `/` from anywhere).
2. **Ink for actions, colour for identity.** Primary buttons are black. Green,
   blue and clay identify libraries; small tints identify source kinds and
   states. No large tinted surfaces.
3. **One stroke, one size family.** Lucide icons at 13–16 px with a 1.75 stroke,
   neutral by default.
4. **Calm type.** A serif for display and long-form reading, a system sans for
   everything you operate.
5. **Motion explains.** Things rise into place, menus pop from their anchor,
   arrows nudge toward where they lead. Nothing loops unless work is happening.

## Colour

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--gx-canvas` | `#ffffff` | `#1f1f1d` | Page background |
| `--gx-sidebar` | `#f9f9f7` | `#1a1a18` | Sidebar and titlebar lead |
| `--gx-surface` / `-2` | `#ffffff` / `#fafaf8` | `#262624` / `#2a2a27` | Cards, wells |
| `--gx-hover` / `--gx-active` | `#f3f3f1` / `#ececea` | `#2f2f2c` / `#373734` | Hover and selected fills |
| `--gx-ink` | `#1a1a18` | `#f3f2ee` | Text, primary buttons |
| `--gx-muted` / `--gx-faint` | `#5f5e5a` / `#75746f` | `#b9b7af` / `#94928b` | Secondary text (both ≥ 4.5:1 on white) |
| `--gx-ghost` | `#a6a59f` | `#6e6d67` | Placeholders and disabled only |
| `--gx-line` / `-2` / `-3` | 8 % / 13 % / 24 % ink | 8 % / 13 % / 24 % white | Hairlines, borders, focus borders |
| `--gx-brand` | `#16745c` | `#5fbf9f` | Brand mark, "saved", grounded / verified, focus ring |

Where colour is allowed:

- **Library identity**: `--gx-id-green`, `--gx-id-blue`, `--gx-id-clay` fill the
  library glyph (sidebar, cards, `@` mentions, results, workspace header).
- **Source kinds**: note = clay, document = blue, web page = amber,
  photo = violet, recording = rose, table = green. Glyph strokes only, on a
  neutral tile. Capture chips reveal their colour on hover.
- **Status**: waiting / attention = clay (`Inbox` count, "Choose a home"),
  grounded / verified / saved = brand green, held = ghost grey,
  errors = `--gx-danger`.

Never tint a page, panel or active navigation item. Selection is expressed
with `--gx-active` and ink text.

## Typography

- Sans: the system UI font (`-apple-system`, SF Pro on macOS). Body 14 px,
  secondary 13 px, meta 12 px, minimum 11 px.
- Display serif (`ui-serif` → New York on macOS): greeting (30–40 px), page
  titles (30 px, weight 450, −0.02 em), library titles on cards (20 px), empty
  state titles. Long-form reading (assistant answers, output documents, quoted
  evidence) also uses the serif.
- Inputs, labels, numbers and buttons are always sans. Numbers use tabular
  figures.

## Iconography

- Lucide, `size` 13–16, stroke forced to 1.75 in CSS. Neutral (`--gx-faint` or
  `--gx-muted`) until hovered or active.
- The **brand mark** (`<BrandMark />`) draws itself in on Home, retraces when
  the wordmark is hovered, and traces continuously with a breathing node while a
  search or answer is in flight.
- The **library glyph** (`<LibraryGlyph />`) is the only filled colour block in
  the interface: 16 / 20 / 28 / 34 px, 4.5–9 px radius, white initial.

## Motion

| Token | Value |
| --- | --- |
| `--gx-dur-fast` | 120 ms — hover fills, colour |
| `--gx-dur` | 180 ms — icon nudges, tab underline |
| `--gx-dur-slow` | 280 ms — page and panel entrances |
| `--gx-ease` | `cubic-bezier(.2, .8, .2, 1)` |
| `--gx-ease-spring` | `cubic-bezier(.34, 1.4, .64, 1)` — chips, badges, the `+` |

Vocabulary (keyframes in `tokens.css`): `gx-rise` (entrances, staggered 32–40 ms
per row via `--i`), `gx-pop` (menus and sheets), `gx-chip-in` (mention chips,
badges), `gx-shimmer` (skeletons), `gx-draw` / `gx-trace` / `gx-breathe`
(brand mark), `gx-spin` (spinners). Micro-interactions: arrows nudge 3 px,
the Capture `+` turns 90°, library glyphs tilt −6°, the theme icon spins in.
`prefers-reduced-motion` collapses every animation and transition.

## Components

- **Buttons**: `gx-btn` + `gx-btn-primary` (ink), `gx-btn-quiet` (white with
  hairline), `gx-btn-ghost` (text); `gx-btn-sm` for dense rows. 34 / 30 px tall,
  8 px radius, press scales to 98.5 %.
- **Composer**: 20 px radius, hairline border that darkens on focus with a soft
  shadow. Toolbar: `@ Library`, `Web` toggle, contextual hint, clear, send.
  Typing `@` at the start of a word opens the library menu (↑ ↓ to move,
  ↵ / Tab to choose, Esc to close); a chosen library becomes a removable chip
  and Backspace at the start removes the last chip. With one library and no
  text, ↵ opens that library; with text, results are scoped to it and an
  **Ask {library}** card hands the question to that library's grounded Ask.
  From the end of the text, ↓ moves into the results; ↑ / ↓ move between
  them, and ↑ from the first result or Esc returns to the search box. Esc in
  the search box clears it and returns to the calm Home.
- **Chips**: 32 px pills for capture kinds.
- **Tabs**: underline tabs with count pills; the active pill is inverted.
- **Rows**: 50 px list rows with a hover fill and a trailing arrow that slides
  in. Result rows stagger in and highlight matched words with a brand wash.
- **Cards**: 14 px radius, hairline, `--gx-shadow-xs`; lift 2 px on hover.
- **Loading**: skeleton rows and cards shimmer until the first data arrives, so
  empty states never flash.
- **Toast**: ink pill, green check, springs up from the bottom right.

## Appearance

Light is the default. Settings offers Light, Dark and **Match system**; the
titlebar toggle switches explicitly between light and dark. The saved choice is
applied before the first paint (`design/theme.ts`), so neither window flashes the
wrong theme, and "Match system" follows macOS as it changes.

## Accessibility

- Text meets WCAG 2.1 AA contrast in both themes (`--gx-faint` is the lightest
  colour allowed for text that carries information; `--gx-ghost` is for
  placeholders and disabled states only).
- One `main` landmark (the workspace), a `banner` titlebar and a navigation
  sidebar; sheets are `section[role=dialog]` with an accessible name.
- Every control has a name that contains its visible label; icon-only buttons
  carry `aria-label`. The `@` menu is a labelled listbox driven by
  `aria-activedescendant`, and result counts are announced as a status.
- Checked with axe-core (WCAG 2.1 A/AA + best practices) across Home, search,
  Libraries, Inbox, the library workspace, Capture, Notebook, Settings and
  Account, in light and dark: zero violations.

## Layout

- Titlebar 44 px; its lead area shares the sidebar colour and width, so the
  sidebar reads as one column under the macOS traffic lights.
- Sidebar 236 px (60–76 px icon rail below 1080 px): Home, Libraries, Inbox,
  your libraries with their glyphs, then Settings and Account.
- Home content width 760 px; Libraries up to 1120 px; Inbox up to 920 px.
- Page header: serif title, one-sentence description, actions on the right.
