/**
 * Units and budgets of the studio deck format (docs/STUDIO_FORMAT.md). A unit is one Latin
 * character or half a CJK character: a CJK character counts 2. The backend mirrors BUDGETS.
 */

// Citation marks: [3], [1, 2], [?], and the Checker's [i:3], [p:3], [d:3].
export const CITATION_MARK = /[ \t]*\[(?:[ipd]:)?(?:\d+(?:\s*[,，、]\s*\d+)*|\?)\]/g;
const CJK = /[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿가-힯＀-￯]/;

/** The visible text of a Markdown slice: no citation marks, no emphasis marks. */
export function visible(raw: string): string {
  return raw.replace(CITATION_MARK, "").replace(/\*\*|__|`/g, "").replace(/\\(.)/g, "$1").trim();
}

/** How long a piece of text is, in units. Citation marks and emphasis marks do not count. */
export function countUnits(raw: string): number {
  let total = 0;
  for (const char of visible(raw)) total += CJK.test(char) ? 2 : 1;
  return total;
}

/** [min, max] units; a field with no minimum has 0. */
export type Range = readonly [number, number];

/** Per layout, per field. Read decks may use READ_FACTOR times the body budgets (titles keep theirs). */
export const BUDGETS = {
  title: { title: [8, 80], subtitle: [0, 160], meta: [0, 80] },
  section: { title: [8, 80], subtitle: [0, 160] },
  all: { title: [12, 100] },
  summary: { items: [2, 4], lead: [0, 60], detail: [0, 130] },
  stat: { value: [1, 16], label: [1, 90], context: [0, 160] },
  stats: { items: [2, 4], value: [1, 10], label: [0, 60], closing: [0, 140] },
  chart: { rows: [2, 12], series: [1, 3], closing: [0, 140] },
  table: { cols: [2, 5], rows: [2, 7], cell: [0, 50], closing: [0, 140] },
  compare: { sides: [2, 2], side: [1, 30], items: [1, 4], item: [1, 90], verdict: [0, 140] },
  process: { items: [2, 6], step: [1, 24], detail: [0, 90] },
  timeline: { items: [3, 6], when: [1, 16], event: [1, 90] },
  quote: { quote: [1, 240], attribution: [0, 90] },
  figure: { takeaway: [0, 160] },
  statement: { statement: [1, 180], items: [0, 3], item: [1, 80] },
  points: { items: [2, 4], lead: [0, 40], detail: [1, 120] },
  actions: { items: [2, 4], lead: [1, 24], detail: [0, 100] },
} as const satisfies Record<string, Record<string, Range>>;

/** Read decks may use this much more than the body budgets above. */
export const READ_FACTOR = 1.5;

export const DECK_LAYOUTS = [
  "title", "section", "summary", "stat", "stats", "chart", "table", "compare", "process",
  "timeline", "quote", "figure", "statement", "points", "actions",
] as const;
export type DeckLayout = (typeof DECK_LAYOUTS)[number];
export const isDeckLayout = (name: string | null | undefined): name is DeckLayout => DECK_LAYOUTS.some((layout) => layout === name);
