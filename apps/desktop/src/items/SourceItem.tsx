import type { SourceDetail } from "@gunther/contracts";
import { ArrowRight, AudioLines, Camera, FileText, Globe, NotebookText, Search, Table2, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { knowledgeApi, recordingAssetUrl, sourceAssetUrl } from "../api";
import type { KnowledgeBase } from "../atlas";
import { countWords } from "../components/markdown/markdownEditing";
import { ClaimsList, FileDecision, ReviewDecision, SettledDecision } from "./Decision";
import { AsideSection, DetailsList, formatDate, ItemHeader, ItemLayout, shortHash, type DetailRow, type IdentityTone, type ItemNavigation } from "./ItemLayout";
import { SOURCE_KIND_LABEL } from "./itemRef";
import {
  formatBytes,
  formatDuration,
  hostOf,
  mediaTypeLabel,
  parseDelimitedTable,
  parseFileContent,
  parseRecordingContent,
  parseResearchContent,
  parseWebContent,
  sourceView,
  withoutRepeatedTitle,
  type SourceView,
} from "./sourceContent";
import { DigestCard } from "./views/DigestCard";
import { DocumentBody } from "./views/DocumentView";
import { ImageBody } from "./views/ImageView";
import { ResearchBody, TextBody, WebBody } from "./views/ReaderViews";
import { RecordingBody } from "./views/RecordingView";
import { TableBody } from "./views/TableView";

const VIEW_IDENTITY: Record<SourceView, { icon: LucideIcon; tone: IdentityTone; label: string }> = {
  recording: { icon: AudioLines, tone: "rose", label: "Recording" },
  web: { icon: Globe, tone: "amber", label: "Web page" },
  research: { icon: Search, tone: "amber", label: "Web research" },
  document: { icon: FileText, tone: "blue", label: "Document" },
  image: { icon: Camera, tone: "violet", label: "Photo or scan" },
  table: { icon: Table2, tone: "green", label: "Table" },
  text: { icon: NotebookText, tone: "clay", label: "Note" },
};

export interface SourceItemProps {
  source: SourceDetail;
  bases: KnowledgeBase[];
  nav: ItemNavigation;
  onResolved: (message: string) => void;
  onOpenBase: (id: string) => void;
  onCreateBase: () => void;
  onNotify: (message: string) => void;
  onTitle: (title: string) => void;
}

export function SourceItem({ source: initial, bases, nav, onResolved, onOpenBase, onCreateBase, onNotify, onTitle }: SourceItemProps) {
  const [source, setSource] = useState(initial);
  useEffect(() => setSource(initial), [initial]);
  useEffect(() => onTitle(source.title), [onTitle, source.title]);

  const view = sourceView(source.kind, source.content, source.asset?.mediaType, Boolean(source.asset));
  // Pasted text keeps the identity of the kind it was captured as.
  const identity = view === "text" && source.kind !== "note"
    ? { ...VIEW_IDENTITY[source.kind === "link" ? "web" : source.kind === "image" ? "image" : source.kind === "table" ? "table" : "document"], label: SOURCE_KIND_LABEL[source.kind] }
    : VIEW_IDENTITY[view];
  const recording = useMemo(() => (view === "recording" ? parseRecordingContent(source.content) : null), [source.content, view]);
  const web = useMemo(() => (view === "web" ? parseWebContent(source.content) : null), [source.content, view]);
  const research = useMemo(() => (view === "research" ? parseResearchContent(source.content) : null), [source.content, view]);
  const file = useMemo(() => (view === "document" || view === "image" ? parseFileContent(source.content) : null), [source.content, view]);
  const table = useMemo(() => (view === "table" ? parseDelimitedTable(source.content) : null), [source.content, view]);

  const libraries = source.knowledgeBases ?? [];
  const pending = source.assertions.filter((assertion) => assertion.status === "provisional").length;
  const trusted = source.assertions.filter((assertion) => assertion.status === "verified").length;
  const state = !libraries.length ? "unfiled" : pending ? "review" : "settled";

  const refresh = async () => {
    const next = await knowledgeApi.source(source.id);
    setSource(next);
    return next;
  };

  const fileTo = async (baseId: string) => {
    try {
      await knowledgeApi.fileSource(source.id, baseId);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      const base = bases.find((item) => item.id === baseId);
      onResolved(`Filed “${source.title}” into ${base?.title ?? "the library"}.${pending ? " Its suggestions wait in Inbox." : ""}`);
    } catch (reason) {
      onNotify(reason instanceof Error ? `Not filed: ${reason.message}` : "This source could not be filed.");
    }
  };

  const reviewAll = async (status: "verified" | "disputed") => {
    try {
      await knowledgeApi.updateSourceAssertionStatuses(source.id, {
        status,
        reason: status === "verified" ? "Accepted from the source page" : "Disputed from the source page",
      });
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      onResolved(status === "verified" ? `Accepted ${pending} ${pending === 1 ? "claim" : "claims"} as trusted knowledge.` : "Claims kept as disputed evidence.");
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The review decision could not be saved.");
    }
  };

  const reviewOne = async (id: string, status: "verified" | "disputed") => {
    try {
      await knowledgeApi.updateAssertionStatus(id, {
        status,
        reason: status === "verified" ? "Reviewed on the source page" : "Disputed on the source page",
      });
      await refresh();
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The review decision could not be saved.");
    }
  };

  const created = formatDate(source.createdAt);
  let kicker = identity.label;
  let meta: Array<string | null> = [created];
  let details: Array<DetailRow | null> = [];
  let body;

  if (recording) {
    kicker = source.kind === "course" ? "Course recording" : "Recording";
    const words = recording.transcript.reduce((total, segment) => total + countWords(segment.text), 0);
    meta = [created, recording.durationSeconds ? formatDuration(recording.durationSeconds) : null, recording.moments.length ? `${recording.moments.length} marked ${recording.moments.length === 1 ? "moment" : "moments"}` : null];
    details = [
      recording.durationSeconds ? { label: "Length", value: formatDuration(recording.durationSeconds) } : null,
      { label: "Recorded", value: recording.capturedLabel ?? formatDate(source.createdAt, true) },
      { label: "Transcript", value: words ? `${words.toLocaleString()} words` : "None" },
      recording.terms.length ? { label: "Terms", value: recording.terms.slice(0, 4).join(", ") + (recording.terms.length > 4 ? "…" : "") } : null,
    ];
    body = <RecordingBody parsed={recording} audioUrl={recording.recordingId ? recordingAssetUrl(recording.recordingId) : null} downloadName={`${source.title}.webm`} />;
  } else if (web) {
    const host = hostOf(web.finalUrl ?? web.originalUrl);
    meta = [host, web.capturedAt ? `Captured ${formatDate(web.capturedAt)}` : created];
    details = [
      host ? { label: "Site", value: host } : null,
      web.originalUrl && web.finalUrl && web.originalUrl !== web.finalUrl ? { label: "Requested", value: web.originalUrl, title: web.originalUrl } : null,
      { label: "Captured", value: formatDate(web.capturedAt ?? source.createdAt, true) },
      web.httpStatus ? { label: "Response", value: `HTTP ${web.httpStatus}${web.contentType ? ` · ${web.contentType.split(";")[0]}` : ""}` } : null,
      web.sha256 ? { label: "Fingerprint", value: <code>{shortHash(web.sha256)}</code>, copy: web.sha256, title: `SHA-256 ${web.sha256}` } : null,
      source.webSnapshot ? { label: "Snapshot", value: <a className="gx-link" href={sourceAssetUrl(source.webSnapshot.assetId)} download>Download original</a> } : null,
    ];
    body = <WebBody web={web} />;
  } else if (research) {
    meta = [created, `${research.references.length} ${research.references.length === 1 ? "page" : "pages"} referenced`];
    details = [{ label: "Query", value: research.query }, { label: "Saved", value: formatDate(source.createdAt, true) }];
    body = <ResearchBody research={research} />;
  } else if (file && (view === "document" || view === "image")) {
    const asset = source.asset;
    const name = asset?.originalName ?? file.fileName;
    const type = mediaTypeLabel(asset?.mediaType ?? file.mediaType, name);
    const size = asset?.sizeBytes ?? file.sizeBytes;
    kicker = view === "image" ? "Photo or scan" : source.kind === "paper" ? "Paper" : "Document";
    meta = [created, type, size ? formatBytes(size) : null];
    details = [
      name ? { label: "File", value: name, title: name } : null,
      { label: "Type", value: type },
      size ? { label: "Size", value: formatBytes(size) } : null,
      file.ocrStatus ? { label: "Text recognition", value: `${file.ocrStatus}${file.ocrProvider ? ` · ${file.ocrProvider}` : ""}` } : null,
      (asset?.contentHash ?? file.sha256) ? { label: "Fingerprint", value: <code>{shortHash(asset?.contentHash ?? file.sha256!)}</code>, copy: asset?.contentHash ?? file.sha256!, title: "SHA-256 of the original file" } : null,
    ];
    body = view === "image" ? <ImageBody source={source} file={file} /> : <DocumentBody source={source} file={file} />;
  } else if (view === "table") {
    meta = [created, table ? `${table.rows.length.toLocaleString()} rows · ${table.header.length} columns` : null];
    details = [
      table ? { label: "Rows", value: table.rows.length.toLocaleString() } : null,
      table ? { label: "Columns", value: table.header.length } : null,
      { label: "Captured", value: formatDate(source.createdAt, true) },
    ];
    body = <TableBody content={source.content} />;
  } else {
    kicker = SOURCE_KIND_LABEL[source.kind];
    const words = countWords(source.content);
    meta = [created, `${words.toLocaleString()} ${words === 1 ? "word" : "words"}`];
    details = [{ label: "Captured", value: formatDate(source.createdAt, true) }, { label: "Length", value: `${words.toLocaleString()} words` }];
    body = <TextBody content={withoutRepeatedTitle(source.content, source.title)} />;
  }

  const aside = (
    <>
      {state === "unfiled" && <FileDecision bases={bases} what="source" onFile={fileTo} onCreateBase={onCreateBase} />}
      {state === "review" && <ReviewDecision base={libraries[0] ?? null} known={bases.find((base) => base.id === libraries[0]?.id)} pending={pending} onOpenBase={onOpenBase} onDecide={reviewAll} />}
      {state === "settled" && (
        <SettledDecision bases={bases} knowledgeBases={libraries} onOpenBase={onOpenBase}>
          {source.assertions.length > 0 && <p className="gx-decision-note">{trusted} of {source.assertions.length} extracted {source.assertions.length === 1 ? "claim is" : "claims are"} trusted.</p>}
          {nav.onNext && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={nav.onNext}>Next item<ArrowRight size={13} /></button>}
        </SettledDecision>
      )}
      {source.assertions.length > 0 && (
        <AsideSection title={pending ? `Suggested claims · ${pending} to review` : "Extracted claims"}>
          <ClaimsList assertions={source.assertions} onDecide={reviewOne} />
        </AsideSection>
      )}
      <AsideSection title="Details">
        <DetailsList rows={[...details, { label: "Kind", value: SOURCE_KIND_LABEL[source.kind] }]} />
      </AsideSection>
    </>
  );

  return (
    <ItemLayout
      nav={nav}
      header={<ItemHeader icon={identity.icon} tone={identity.tone} kicker={kicker} title={source.title} meta={meta} />}
      aside={aside}
    >
      {/* Written after the capture is read; a placeholder title may change with it. */}
      <DigestCard sourceId={source.id} onWritten={() => { void refresh().catch(() => undefined); }} />
      {body}
    </ItemLayout>
  );
}
