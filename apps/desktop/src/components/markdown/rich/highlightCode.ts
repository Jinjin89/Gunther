import { createHighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import { bundledLanguages } from "shiki/langs";
import type { ThemedToken } from "shiki/types";

const themes = { light: "github-light", dark: "github-dark" };
let ready: ReturnType<typeof createHighlighterCore> | undefined;
const loaded = new WeakMap<() => unknown, Promise<void>>();

export async function highlightCode(code: string, language: string): Promise<ThemedToken[][] | null> {
  const lang = language.toLowerCase() as keyof typeof bundledLanguages;
  if (!Object.prototype.hasOwnProperty.call(bundledLanguages, lang)) return null;
  const load = bundledLanguages[lang];
  const highlighter = await (ready ??= createHighlighterCore({
    engine: createJavaScriptRegexEngine(),
    themes: [import("shiki/themes/github-light.mjs"), import("shiki/themes/github-dark.mjs")],
  }).catch((error: unknown) => {
    ready = undefined;
    throw error;
  }));

  let grammar = loaded.get(load);
  if (!grammar) {
    grammar = highlighter.loadLanguage(load).catch((error: unknown) => {
      loaded.delete(load);
      throw error;
    });
    loaded.set(load, grammar);
  }
  await grammar;
  return highlighter.codeToTokens(code, { lang, themes, defaultColor: false }).tokens;
}
