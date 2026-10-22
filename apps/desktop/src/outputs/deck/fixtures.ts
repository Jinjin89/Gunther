const files = import.meta.glob("./fixtures/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

/** The fixture decks and report (Markdown files shared with the backend's grammar tests), for tests. */
export const fixture = (name: "deck.short" | "deck.max" | "deck.zh" | "report"): string => files[`./fixtures/${name}.md`] ?? "";
