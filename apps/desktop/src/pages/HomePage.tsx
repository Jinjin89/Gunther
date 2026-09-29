import type { KnowledgeSearchResult, SourceSummary } from "@gunther/contracts";
import {
  ArrowRight,
  Camera,
  Check,
  FileText,
  Link2,
  Mic,
  MoreHorizontal,
  NotebookPen,
  Plus,
  Table2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import type { AtlasMode, KnowledgeBase } from "../atlas";
import { knowledgeApi } from "../api";
import { HomeAsk, RecentQuestions } from "../components/search/HomeAsk";
import { SearchComposer, type SearchComposerHandle } from "../components/search/SearchComposer";
import { SearchResults } from "../components/search/SearchResults";
import { useKnowledgeSearch } from "../components/search/useKnowledgeSearch";
import { BrandMark } from "../design/BrandMark";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { refFromSearch, type ItemRef } from "../items/itemRef";
import { usableChoice, useDeviceChoice, useModelMenu, WEB_PREFERENCE_KEY } from "../models/askModel";
import { ModelPicker } from "../models/ModelPicker";
import { useHomeAsk } from "./useHomeAsk";

export type HomeCaptureKind = "note" | "link" | "file" | "image" | "recording" | "table";

interface HomePageProps {
  bases: KnowledgeBase[];
  /** False until the first library list has loaded, so empty states never flash. */
  basesReady?: boolean;
  inboxCount: number;
  profileName?: string;
  /** Increment to move keyboard focus into the search composer. */
  focusRequest?: number;
  onCapture: (kind?: HomeCaptureKind) => void;
  onOpenBase: (id: string, mode?: AtlasMode) => void;
  onOpenChapter: (baseId: string, chapterId: string) => void;
  onAskBase: (id: string, question: string) => void;
  onOpenNote: (id: string) => void;
  /** Open a note or source in its own page; `queue` is what J / K step through. */
  onOpenItem?: (item: ItemRef, queue: ItemRef[]) => void;
  /** Coming back from an opened result: restore the search it came from. */
  resumeSearch?: boolean;
  onOpenLibraries: () => void;
  onCreateBase: () => void;
  onOpenInbox: () => void;
  onNotify: (message: string) => void;
  resolveWorkspaceId: () => Promise<string>;
}


const captureActions = [
  { id: "note", label: "Note", title: "Quick note — write a thought before it disappears", icon: NotebookPen, tone: "clay" },
  { id: "file", label: "Document", title: "Document — PDF, paper, slides, or text", icon: FileText, tone: "blue" },
  { id: "image", label: "Photo", title: "Photo or scan — a page, board, or diagram", icon: Camera, tone: "violet" },
  { id: "link", label: "Web page", title: "Web page — save a link with your own context", icon: Link2, tone: "amber" },
  { id: "recording", label: "Recording", title: "Recording — live transcript or imported audio", icon: Mic, tone: "rose" },
  { id: "table", label: "Table", title: "Table or data — paste rows with their header", icon: Table2, tone: "green" },
] as const;

const sourceLabel = (source: SourceSummary) => ({
  note: "Note",
  paper: "Paper",
  link: "Web page",
  file: "Document",
  image: "Photo or scan",
  table: "Dataset",
  recording: "Recording",
  course: "Course",
}[source.kind]);

const sourceIcon = (source: SourceSummary) => ({
  note: NotebookPen,
  paper: FileText,
  link: Link2,
  file: FileText,
  image: Camera,
  table: Table2,
  recording: Mic,
  course: Mic,
}[source.kind]);

const greetingFor = (date: Date) => {
  const hour = date.getHours();
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
};

const formatDay = (value: string) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });

const plural = (count: number, singular: string) => `${count} ${singular}${count === 1 ? "" : "s"}`;

/** The search a result was opened from, so Back returns to the same results. */
let rememberedSearch: { query: string; mentionIds: string[]; web: boolean } | null = null;

function RowSkeletons() {
  return <>{[0, 1, 2].map((index) => <div className="gx-row-skeleton" key={index} aria-hidden="true"><i /><span><b /><b /></span></div>)}</>;
}

/** Search-first home: find anything, point at a library with @, or capture something new. */
export function HomePage({
  bases,
  basesReady = true,
  inboxCount,
  profileName = "",
  focusRequest = 0,
  onCapture,
  onOpenBase,
  onOpenChapter,
  onAskBase,
  onOpenNote,
  onOpenItem,
  resumeSearch = false,
  onOpenLibraries,
  onCreateBase,
  onOpenInbox,
  onNotify,
  resolveWorkspaceId,
}: HomePageProps) {
  const [recentSources, setRecentSources] = useState<SourceSummary[]>([]);
  const [sourcesReady, setSourcesReady] = useState(false);
  const [query, setQuery] = useState("");
  const [mentionIds, setMentionIds] = useState<string[]>([]);
  const [web, setWeb] = useState(() => window.localStorage.getItem(WEB_PREFERENCE_KEY) === "on");
  const [savingResearch, setSavingResearch] = useState(false);
  const [savedResearchQuery, setSavedResearchQuery] = useState<string | null>(null);
  const composer = useRef<SearchComposerHandle>(null);
  const search = useKnowledgeSearch(bases);
  // The model that answers when a search turns into a question (Ask this library).
  const { menu: modelMenu } = useModelMenu();
  const [deviceChoice, setDeviceChoice] = useDeviceChoice();
  const { submitted, run, reset } = search;
  const homeAsk = useHomeAsk();

  useEffect(() => {
    let active = true;
    void knowledgeApi.sources()
      .then((items) => { if (active) setRecentSources(items.slice(0, 4)); })
      .catch(() => undefined)
      .finally(() => { if (active) setSourcesReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (focusRequest > 0) composer.current?.focus();
  }, [focusRequest]);

  // Returning from a result restores the search it was opened from.
  useEffect(() => {
    if (!resumeSearch || !rememberedSearch) return;
    const { query: previous, mentionIds: previousMentions, web: previousWeb } = rememberedSearch;
    setQuery(previous);
    setMentionIds(previousMentions);
    void run(previous, previousWeb ? "both" : "knowledge", previousMentions);
    // Only on arrival.
  }, []);

  // A library that disappears (renamed away or deleted) must not stay as a hidden scope.
  useEffect(() => {
    setMentionIds((current) => {
      const next = current.filter((id) => bases.some((base) => base.id === id));
      return next.length === current.length ? current : next;
    });
  }, [bases]);

  const scope = web ? "both" : "knowledge";

  const submit = () => {
    const needle = query.trim();
    if (!needle && mentionIds.length === 1) {
      onOpenBase(mentionIds[0]!);
      return;
    }
    if (needle) void run(needle, scope, mentionIds);
  };

  const changeWeb = (next: boolean) => {
    setWeb(next);
    window.localStorage.setItem(WEB_PREFERENCE_KEY, next ? "on" : "off");
    if (submitted) void run(submitted.query, next ? "both" : "knowledge", submitted.baseIds);
  };

  const changeMentions = (ids: string[]) => {
    setMentionIds(ids);
    if (submitted) void run(submitted.query, submitted.scope, ids);
  };

  const focusFirstResult = () => {
    const first = document.querySelector<HTMLElement>(".gx-results [data-result-item]");
    first?.focus();
    return Boolean(first);
  };

  const askNow = (question = query) => {
    const text = question.trim();
    if (!text) return;
    const choice = usableChoice(modelMenu, deviceChoice);
    void homeAsk.ask(text, {
      ...(choice.model && modelMenu?.models.length ? { model: choice.model, effort: choice.effort } : {}),
      web,
      libraryIds: mentionIds,
    });
  };

  const fileConversation = async (libraryId: string) => {
    const sessionId = await homeAsk.file(libraryId);
    if (!sessionId) return;
    window.localStorage.setItem(`gunther:active-session:${libraryId}`, sessionId);
    onNotify("Conversation filed in the library.");
    onOpenBase(libraryId, "ask");
  };

  const clear = () => {
    homeAsk.close();
    setQuery("");
    setMentionIds([]);
    rememberedSearch = null;
    reset();
  };

  const openResult = useCallback((result: KnowledgeSearchResult) => {
    const item = refFromSearch(result);
    if (item && onOpenItem) {
      if (submitted) rememberedSearch = { query: submitted.query, mentionIds: submitted.baseIds, web: submitted.scope !== "knowledge" };
      const queue = search.indexedResults.map(refFromSearch).filter((ref): ref is ItemRef => ref !== null);
      onOpenItem(item, queue);
      return;
    }
    if (result.kind === "note") {
      onOpenNote(result.id);
      return;
    }
    const baseId = result.knowledgeBaseId;
    if (!baseId) {
      // Unfiled captures live in Inbox until they are given a home.
      onOpenInbox();
      return;
    }
    if (result.kind === "session") {
      window.localStorage.setItem(`gunther:active-session:${baseId}`, result.id);
      onOpenBase(baseId, "ask");
      return;
    }
    if (result.kind === "source") {
      window.localStorage.setItem(`gunther:open-source:${baseId}`, result.id);
      onOpenBase(baseId, "sources");
      return;
    }
    if (result.kind === "knowledge_unit" && result.sourceSessionId) {
      window.localStorage.setItem(`gunther:active-session:${baseId}`, result.sourceSessionId);
      if (result.sourceMessageId) window.localStorage.setItem(`gunther:selected-message:${baseId}`, result.sourceMessageId);
      onOpenBase(baseId, "ask");
      return;
    }
    onOpenBase(baseId);
  }, [onOpenBase, onOpenInbox, onOpenItem, onOpenNote, search.indexedResults, submitted]);

  const saveResearch = async () => {
    const webResult = search.webResult;
    if (!webResult || webResult.mode !== "tavily" || !(webResult.answer.trim() || webResult.sources.length)) return;
    setSavingResearch(true);
    const references = webResult.sources.map((source, index) => [
      `${index + 1}. ${source.title}`,
      source.url,
      source.snippet?.trim() || null,
    ].filter(Boolean).join("\n")).join("\n\n");
    const content = [
      `Search query: ${webResult.query}`,
      "",
      webResult.answer ? "Answer captured from web research:" : "",
      webResult.answer,
      references ? "\nReferenced pages:\n" + references : "",
    ].join("\n").trim();
    try {
      const expectedWorkspaceId = await resolveWorkspaceId();
      await knowledgeApi.importSource({
        title: `Web research · ${webResult.query}`.slice(0, 160),
        kind: "link",
        content,
      }, expectedWorkspaceId);
      setSavedResearchQuery(webResult.query);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      onNotify("Web research preserved in Inbox with its referenced URLs.");
    } catch (reason) {
      onNotify(reason instanceof Error ? `Research not saved: ${reason.message}` : "This research could not be saved.");
    } finally {
      setSavingResearch(false);
    }
  };

  const hasResults = Boolean(submitted);
  const firstName = profileName.trim().split(/\s+/)[0] ?? "";
  const greeting = `${greetingFor(new Date())}${firstName ? `, ${firstName}` : ""}`;
  const recentBases = bases.slice(0, 3);

  return (
    <div className={`gx-home ${hasResults ? "has-results" : "is-idle"}`}>
      <section className="gx-home-hero">
        <h1 className="gx-greeting" aria-hidden={hasResults}>
          <BrandMark size={30} animated busy={search.searching} className="gx-greeting-mark" />
          <span>{greeting}</span>
        </h1>
        <SearchComposer
          ref={composer}
          bases={bases}
          value={query}
          mentionIds={mentionIds}
          web={web}
          searching={search.searching}
          placeholder="Search your knowledge, or type @ to pick a library"
          onValueChange={setQuery}
          onMentionsChange={changeMentions}
          onWebChange={changeWeb}
          onSubmit={submit}
          onAsk={() => askNow()}
          onClear={clear}
          onArrowDown={submitted ? focusFirstResult : undefined}
          picker={<ModelPicker menu={modelMenu} choice={usableChoice(modelMenu, deviceChoice)} onChange={setDeviceChoice} placement="down" />}
        />
        {!hasResults && (
          <div className="gx-capture-row" role="group" aria-label="Capture">
            {captureActions.map(({ id, label, title, icon: Icon, tone }, index) => (
              <button type="button" key={id} className={`gx-chip tone-${tone}`} style={{ "--i": index } as CSSProperties} onClick={() => onCapture(id)} title={title} aria-label={title.split(" — ")[0]}>
                <Icon size={15} />
                <span>{label}</span>
              </button>
            ))}
            <button type="button" className="gx-chip gx-chip-icon" style={{ "--i": captureActions.length } as CSSProperties} onClick={() => onCapture()} aria-label="All capture options" title="All capture options">
              <MoreHorizontal size={15} />
            </button>
          </div>
        )}
      </section>

      <HomeAsk
        query={hasResults ? query : ""}
        bases={bases}
        messages={homeAsk.messages}
        pending={homeAsk.pending}
        live={homeAsk.live}
        error={homeAsk.error}
        active={homeAsk.shown}
        onAsk={askNow}
        onCancel={homeAsk.cancel}
        onClose={homeAsk.close}
        onNew={homeAsk.fresh}
        onFile={(libraryId) => void fileConversation(libraryId)}
        onOpenSource={(item) => onOpenItem?.(item, [item])}
      />

      {hasResults && submitted ? (
        <SearchResults
          bases={bases}
          submitted={submitted}
          indexedResults={search.indexedResults}
          curatedResults={search.curatedResults}
          webResult={search.webResult}
          searching={search.searching}
          error={search.error}
          savingResearch={savingResearch}
          researchSaved={Boolean(search.webResult) && savedResearchQuery === search.webResult?.query}
          onOpenResult={openResult}
          onOpenBase={(id) => onOpenBase(id)}
          onOpenChapter={onOpenChapter}
          onAsk={onAskBase}
          onSaveResearch={() => void saveResearch()}
          onExit={() => composer.current?.focus()}
        />
      ) : (
        <section className="gx-home-recent" aria-label="Jump back in">
          <button type="button" className={`gx-inbox-line ${inboxCount ? "has-items" : ""}`} onClick={onOpenInbox}>
            {inboxCount
              ? <span className="gx-inbox-count">{inboxCount}</span>
              : <span className="gx-inbox-check"><Check size={13} /></span>}
            <span className="gx-inbox-copy">
              <strong>{inboxCount ? `${plural(inboxCount, "item")} waiting in Inbox` : "Inbox is clear"}</strong>
              <span>{inboxCount ? "Nothing becomes trusted knowledge without your decision." : "New captures wait there until you choose a home."}</span>
            </span>
            <ArrowRight size={14} className="gx-nudge" />
          </button>

          {!homeAsk.shown && <RecentQuestions items={homeAsk.recent} onOpen={(id) => void homeAsk.open(id)} />}

          <div className="gx-home-columns">
            <div className="gx-home-column">
              <header>
                <h2>Libraries</h2>
                <button type="button" className="gx-link" onClick={onOpenLibraries}>View all</button>
              </header>
              {recentBases.map((base) => (
                <button type="button" className="gx-row" key={base.id} onClick={() => onOpenBase(base.id)}>
                  <LibraryGlyph base={base} size="md" />
                  <span className="gx-row-body">
                    <strong>{base.title}</strong>
                    <small>{plural(base.indexedSourceCount ?? base.sourceCount, "source")} · {plural(base.chapterCount, "chapter")}</small>
                  </span>
                  <ArrowRight size={14} className="gx-row-arrow" />
                </button>
              ))}
              {!basesReady && <RowSkeletons />}
              {basesReady && recentBases.length === 0 && (
                <div className="gx-empty-inline">
                  <span><strong>No libraries yet.</strong> Give a subject a home once it becomes clear — captures can wait in Inbox.</span>
                  <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onCreateBase}><Plus size={13} />New library</button>
                </div>
              )}
            </div>

            <div className="gx-home-column">
              <header><h2>Recently captured</h2></header>
              {recentSources.map((source) => {
                const Icon = sourceIcon(source);
                const queue = recentSources.map((item): ItemRef => ({ type: "source", id: item.id }));
                return (
                  <button type="button" className="gx-row" key={source.id} onClick={() => onOpenItem?.({ type: "source", id: source.id }, queue)}>
                    <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={15} /></span>
                    <span className="gx-row-body">
                      <strong>{source.title}</strong>
                      <small>{sourceLabel(source)} · {formatDay(source.createdAt)}</small>
                    </span>
                    {source.assertionCount > 0 && <em>{plural(source.assertionCount, "claim")}</em>}
                    <ArrowRight size={14} className="gx-row-arrow" />
                  </button>
                );
              })}
              {!sourcesReady && <RowSkeletons />}
              {sourcesReady && recentSources.length === 0 && (
                <div className="gx-empty-inline"><span>Notes, files, pages and recordings you capture show up here first.</span></div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
