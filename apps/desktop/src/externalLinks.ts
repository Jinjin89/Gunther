import { invoke } from "@tauri-apps/api/core";
import { message, save } from "@tauri-apps/plugin-dialog";
import { writeFile } from "@tauri-apps/plugin-fs";

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

const fileNameOf = (anchor: HTMLAnchorElement, response: Response) => {
  const named = anchor.getAttribute("download")?.trim();
  if (named) return named;
  const header = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get("content-disposition") ?? "")?.[1];
  if (header) {
    try { return decodeURIComponent(header); } catch { return header; }
  }
  return decodeURIComponent(new URL(anchor.href).pathname.split("/").pop() || "download");
};

/**
 * The desktop web view cannot download: following a `download` link would
 * replace the whole app with the file. Ask where to save it, then write it.
 */
export async function saveDownload(anchor: HTMLAnchorElement): Promise<void> {
  const response = await fetch(anchor.href, { cache: "no-store" }).catch((error) => {
    throw new Error(`The file could not be fetched from ${new URL(anchor.href).origin}: ${error instanceof Error ? error.message : error}`);
  });
  if (!response.ok) throw new Error(`The file could not be fetched (${response.status}).`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const path = await save({ defaultPath: fileNameOf(anchor, response) });
  if (path) await writeFile(path, bytes);
}

/**
 * WebKit ignores `target="_blank"` inside the desktop app, so external links
 * would silently do nothing. Route them to the user's browser or mail app.
 */
export function installExternalLinkHandler(): void {
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const download = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[download][href]") : null;
    if (download) {
      event.preventDefault();
      void saveDownload(download).catch((error) => message(`${error instanceof Error ? error.message : String(error)}`, { title: "The file could not be saved", kind: "error" }));
      return;
    }
    const url = externalLinkFor(event.target, window.location.origin);
    if (!url) return;
    event.preventDefault();
    void invoke("open_external_url", { url }).catch(() => undefined);
  }, true);
}
