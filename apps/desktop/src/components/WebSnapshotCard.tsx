import type { WebSnapshot } from "@gunther/contracts";
import { Download, ExternalLink, ShieldCheck } from "lucide-react";
import { sourceAssetUrl } from "../api";

function safeHttpUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && !parsed.username && !parsed.password
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

function readableUrl(value: string): string {
  try {
    const parsed = new URL(value);
    return `${parsed.host}${parsed.pathname === "/" ? "" : parsed.pathname}${parsed.search}`;
  } catch {
    return value;
  }
}

export function WebSnapshotCard({ snapshot }: { snapshot: WebSnapshot }) {
  const originalUrl = safeHttpUrl(snapshot.originalUrl);
  const finalUrl = safeHttpUrl(snapshot.finalUrl);
  const redirected = snapshot.originalUrl !== snapshot.finalUrl;
  const capturedAt = new Date(snapshot.capturedAt);
  const capturedLabel = Number.isNaN(capturedAt.getTime())
    ? snapshot.capturedAt
    : capturedAt.toLocaleString();

  return (
    <section className="web-snapshot-card" aria-label="Web snapshot provenance">
      <header>
        <span className="web-snapshot-seal"><ShieldCheck size={16} /></span>
        <span><h3>Immutable web snapshot</h3><small>Original response bytes and capture trail preserved</small></span>
        <i>Preserved</i>
      </header>
      <dl>
        <div><dt>Requested</dt><dd title={snapshot.originalUrl}>{originalUrl ? <a href={originalUrl} target="_blank" rel="noreferrer">{readableUrl(snapshot.originalUrl)}</a> : readableUrl(snapshot.originalUrl)}</dd></div>
        {redirected && <div><dt>Resolved to</dt><dd title={snapshot.finalUrl}>{finalUrl ? <a href={finalUrl} target="_blank" rel="noreferrer">{readableUrl(snapshot.finalUrl)}</a> : readableUrl(snapshot.finalUrl)}</dd></div>}
        <div><dt>Captured</dt><dd>{capturedLabel}</dd></div>
        <div><dt>Response</dt><dd title={`HTTP ${snapshot.status} · ${snapshot.contentType}`}>HTTP {snapshot.status} · {snapshot.contentType}</dd></div>
        <div className="web-snapshot-hash"><dt>SHA-256</dt><dd><code title={snapshot.contentHash}>{snapshot.contentHash}</code></dd></div>
      </dl>
      <footer>
        {finalUrl && <a href={finalUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open current page</a>}
        <a href={sourceAssetUrl(snapshot.assetId)} download><Download size={13} />Download preserved snapshot</a>
      </footer>
    </section>
  );
}
