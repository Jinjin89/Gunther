import { describe, expect, it } from "vitest";
import { externalLinkFor } from "./externalLinks";

const link = (href: string, attributes: Record<string, string> = {}) => {
  const anchor = document.createElement("a");
  anchor.setAttribute("href", href);
  Object.entries(attributes).forEach(([name, value]) => anchor.setAttribute(name, value));
  const inner = document.createElement("span");
  anchor.append(inner);
  document.body.append(anchor);
  return inner;
};

describe("external links", () => {
  const origin = "tauri://localhost";

  it("hands web and mail links to the system", () => {
    expect(externalLinkFor(link("https://example.com/page", { target: "_blank" }), origin)).toBe("https://example.com/page");
    expect(externalLinkFor(link("mailto:someone@example.com"), origin)).toBe("mailto:someone@example.com");
  });

  it("keeps downloads, in-app anchors and the private local service inside the app", () => {
    expect(externalLinkFor(link("https://example.com/file.pdf", { download: "" }), origin)).toBeNull();
    expect(externalLinkFor(link("#user-content-fn-1"), origin)).toBeNull();
    expect(externalLinkFor(link("http://127.0.0.1:8787/api/assets/ast_1?token=secret", { target: "_blank" }), origin)).toBeNull();
    expect(externalLinkFor(link("javascript:alert(1)"), origin)).toBeNull();
    expect(externalLinkFor(document.createElement("span"), origin)).toBeNull();
  });
});
