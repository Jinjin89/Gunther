const squash = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();
const escapeHtml = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Marks the text runs on a page that belong to the cited passage. */
export function highlightRenderer(quote: string) {
  const needle = squash(quote);
  return ({ str }: { str: string }): string => {
    const run = squash(str);
    return run.length >= 4 && needle.includes(run) ? `<mark>${escapeHtml(str)}</mark>` : escapeHtml(str);
  };
}
