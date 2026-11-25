import { isTokenWhiteSpaceOrComment, tokenize, TokenType, type CSSToken } from "@csstools/css-tokenizer";

// These functions can construct an image or URL from a string rather than a url() token.
const RESOURCE_FUNCTIONS = new Set(["image", "image-set", "-webkit-image-set", "src"]);

function closingParen(tokens: CSSToken[], index: number): number {
  let depth = 1;
  while (++index < tokens.length) {
    const kind = tokens[index]?.[0];
    if (kind === TokenType.Function || kind === TokenType.OpenParen) depth += 1;
    if (kind === TokenType.CloseParen && --depth === 0) break;
  }
  return index;
}

/** Keep same-document URLs in styles, declarations and SVG presentation values. */
export function localSvgCss(css: string): string {
  const tokens = tokenize({ css });
  let clean = "";
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index]!;
    const kind = token[0];
    if (kind === TokenType.AtKeyword && token[4].value.toLowerCase() === "import") {
      // An import can fetch a stylesheet even when its argument is a string or a fragment.
      while (++index < tokens.length && tokens[index]?.[0] !== TokenType.Semicolon) {
        const part = tokens[index]?.[0];
        if (part === TokenType.Function || part === TokenType.OpenParen) index = closingParen(tokens, index);
      }
      clean += " ";
      continue;
    }
    if (kind === TokenType.BadURL || (kind === TokenType.URL && !token[4].value.startsWith("#"))) {
      clean += " none ";
      continue;
    }
    if (kind === TokenType.Function) {
      const name = token[4].value.toLowerCase();
      if (name === "url" || RESOURCE_FUNCTIONS.has(name)) {
        const end = closingParen(tokens, index);
        const args = tokens.slice(index + 1, end).filter((part) => !isTokenWhiteSpaceOrComment(part));
        const value = args[0];
        const local = name === "url" && args.length === 1 && value?.[0] === TokenType.String && value[4].value.startsWith("#");
        if (!local) {
          clean += " none ";
          index = end;
          continue;
        }
      }
    }
    clean += token[1];
  }
  return clean;
}
