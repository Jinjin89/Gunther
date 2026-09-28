import { invoke } from "@tauri-apps/api/core";

const LOCAL_SERVICE = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** The external web or mail link a click should hand to the system, if any. */
export function externalLinkFor(target: EventTarget | null, pageOrigin: string): string | null {
  const anchor = target instanceof Element ? target.closest<HTMLAnchorElement>("a[href]") : null;
  if (!anchor || anchor.hasAttribute("download")) return null;
  let url: URL;
  try {
    url = new URL(anchor.href, pageOrigin);
  } catch {
    return null;
  }
  if (url.protocol === "mailto:") return url.href;
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  // Links to Gunther's own service carry its private token; they never leave the app.
  if (LOCAL_SERVICE.has(url.hostname)) return null;
  if (url.origin === pageOrigin && anchor.target !== "_blank") return null;
  return url.href;
}

/**
 * WebKit ignores `target="_blank"` inside the desktop app, so external links
 * would silently do nothing. Route them to the user's browser or mail app.
 */
export function installExternalLinkHandler(): void {
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = externalLinkFor(event.target, window.location.origin);
    if (!url) return;
    event.preventDefault();
    void invoke("open_external_url", { url }).catch(() => undefined);
  }, true);
}
