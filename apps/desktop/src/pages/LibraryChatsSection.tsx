import type { KnowledgeSessionSummary } from "@gunther/contracts";
import { Archive, ChevronDown, ChevronRight, GitBranch, MoreHorizontal, Pin, Plus, Search, SquarePen, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { persistentStorage } from "../persistentStorage";
import type { UseLibraryChats } from "./useLibraryChats";
import "./libraryChats.css";

const RECENT_LIMIT = 4;

export function relativeChatTime(value: string) {
  const elapsed = Date.now() - new Date(value).getTime();
  if (elapsed < 60_000) return "Now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}d`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}

function ChatRow({
  session,
  parentTitle,
  active,
  renaming,
  onStartRename,
  onRename,
  onCancelRename,
  onOpen,
  onPin,
  onArchive,
}: {
  session: KnowledgeSessionSummary;
  parentTitle?: string | undefined;
  active: boolean;
  renaming: boolean;
  onStartRename: () => void;
  onRename: (title: string) => void;
  onCancelRename: () => void;
  onOpen: () => void;
  onPin: () => void;
  onArchive: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [title, setTitle] = useState(session.title);
  useEffect(() => setTitle(session.title), [session.title]);
  const cancelRename = () => {
    setTitle(session.title);
    onCancelRename();
  };
  if (renaming) {
    return <form className="library-chat-row is-editing" onSubmit={(event) => {
      event.preventDefault();
      onRename(title.trim());
    }}>
      <input autoFocus value={title} aria-label="Chat title" onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancelRename(); } }} />
      <button type="submit" aria-label="Save chat title">Save</button>
      <button type="button" aria-label="Cancel renaming" onClick={cancelRename}>Cancel</button>
    </form>;
  }
  return (
    <div className={`library-chat-row ${active ? "is-active" : ""} ${session.archived ? "is-archived" : ""}`}
      onKeyDown={(event) => { if (event.key === "Escape") setMenuOpen(false); }}>
      <button type="button" className="library-chat-open" onClick={onOpen} aria-current={active ? "page" : undefined}>
        <span>{session.title}</span>
        {parentTitle && <span className="library-chat-branch" title={`Branched from ${parentTitle}`}>
          <GitBranch size={11} aria-hidden />
        </span>}
        {session.pinned && <Pin className="library-chat-pin" size={10} fill="currentColor" aria-label="Pinned" />}
        <time>{relativeChatTime(session.updatedAt)}</time>
      </button>
      <button type="button" className="library-chat-more" aria-label={`Actions for ${session.title}`}
        aria-expanded={menuOpen} aria-haspopup="menu" onClick={() => setMenuOpen((value) => !value)}>
        <MoreHorizontal size={13} />
      </button>
      {menuOpen && <div className="library-chat-menu" role="menu" onMouseLeave={() => setMenuOpen(false)}>
        <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onStartRename(); }}>
          <SquarePen size={12} />Rename
        </button>
        <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onPin(); }}>
          <Pin size={12} />{session.pinned ? "Unpin" : "Pin"}
        </button>
        <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onArchive(); }}>
          <Archive size={12} />{session.archived ? "Restore" : "Archive"}
        </button>
      </div>}
    </div>
  );
}

export function LibraryChatsSection({ baseId, chats, onOpen, onCreate }: {
  baseId: string;
  chats: UseLibraryChats;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  const storageKey = `gunther:library-chats-collapsed:${baseId}`;
  const [collapsed, setCollapsed] = useState(() => persistentStorage.getItem(storageKey) === "1");
  const [allOpen, setAllOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const parents = useMemo(() => new Map([...chats.sessions, ...chats.archivedSessions]
    .map((session) => [session.id, session.title])), [chats.archivedSessions, chats.sessions]);
  const searching = query.trim() !== "";
  const liveMatches = useMemo(() => chats.sessions.filter((session) =>
    `${session.title} ${session.summary}`.toLowerCase().includes(query.trim().toLowerCase())), [chats.sessions, query]);
  const pinned = liveMatches.filter((session) => session.pinned);
  const recent = liveMatches.filter((session) => !session.pinned);
  const compactRows = [...pinned, ...recent.slice(0, RECENT_LIMIT)];
  const expandedRows = allOpen || searchOpen ? liveMatches : compactRows;
  const archivedMatches = useMemo(() => chats.archivedSessions.filter((session) =>
    `${session.title} ${session.summary}`.toLowerCase().includes(query.trim().toLowerCase())), [chats.archivedSessions, query]);
  const showAllLink = !allOpen && !searchOpen && chats.sessions.length > compactRows.length;
  const setSectionCollapsed = (next: boolean) => {
    setCollapsed(next);
    persistentStorage.setItem(storageKey, next ? "1" : "0");
    if (next && chats.showArchived) void chats.toggleArchived();
  };
  const open = (id: string) => {
    setRenaming(null);
    onOpen(id);
  };
  const rename = async (id: string, title: string) => {
    if (!title) return;
    try {
      await chats.patchSession(id, { title });
      setRenaming(null);
      setActionError(null);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not rename the chat");
    }
  };
  const row = (session: KnowledgeSessionSummary) => <ChatRow
    key={session.id} session={session} active={chats.activeId === session.id}
    parentTitle={session.parentSessionId ? parents.get(session.parentSessionId) : undefined}
    renaming={renaming === session.id}
    onStartRename={() => setRenaming(session.id)}
    onRename={(title) => void rename(session.id, title)}
    onCancelRename={() => setRenaming(null)}
    onOpen={() => open(session.id)}
    onPin={() => { setRenaming(null); void chats.pinSession(session); }}
    onArchive={() => { setRenaming(null); void (session.archived ? chats.restoreSession(session) : chats.archiveSession(session)); }} />;

  return <section className={`library-chats ${collapsed ? "is-collapsed" : ""}`} aria-label="Chats">
    <header className="library-chats-heading">
      <button type="button" className="library-chats-title" aria-expanded={!collapsed}
        aria-controls="library-chats-body" onClick={() => setSectionCollapsed(!collapsed)}>
        {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        <span>Chats</span>
        <small>{chats.sessions.length}</small>
      </button>
      {!collapsed && <>
        <button type="button" className="library-chats-icon" aria-label="New chat" disabled={!chats.online || chats.loading} onClick={onCreate}>
          <Plus size={13} />
        </button>
        <button type="button" className={`library-chats-icon ${searchOpen ? "is-on" : ""}`} aria-label="Search chats"
          aria-pressed={searchOpen} onClick={() => {
            setSearchOpen((value) => !value);
            if (searchOpen) {
              setQuery("");
              if (!allOpen && chats.showArchived) void chats.toggleArchived();
            }
          }}>
          <Search size={13} />
        </button>
      </>}
    </header>
    <div id="library-chats-body" className="library-chats-body" hidden={collapsed}>
      {searchOpen && <label className="library-chats-search">
        <Search size={12} />
        <input value={query} placeholder="Search chats" aria-label="Search chats" onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            if (query) setQuery("");
            else {
              setSearchOpen(false);
              setAllOpen(false);
              if (chats.showArchived) void chats.toggleArchived();
            }
          }} />
        {query && <button type="button" aria-label="Clear chat search" onClick={() => setQuery("")}><X size={11} /></button>}
      </label>}
      {chats.loading && <p className="library-chats-status" role="status">Loading chats…</p>}
      {!chats.loading && chats.readError && <p className="library-chats-error" role="alert">
        Could not load chats · <button type="button" onClick={() => void chats.load()}>Retry</button>
      </p>}
      {!chats.loading && !chats.readError && expandedRows.map(row)}
      {!chats.loading && !chats.readError && (showAllLink || allOpen) && <button type="button" className="library-chats-disclosure"
        aria-expanded={allOpen} onClick={() => {
          if (allOpen && !searchOpen && chats.showArchived) void chats.toggleArchived();
          setAllOpen(!allOpen);
        }}>
        All chats ({chats.sessions.length})
      </button>}
      {!chats.loading && !chats.readError && searching && liveMatches.length === 0 && archivedMatches.length === 0
        && <p className="library-chats-status">No matching chats</p>}
      {!chats.loading && !chats.readError && (allOpen || searchOpen) && <button type="button" className="library-chats-disclosure"
        aria-expanded={chats.showArchived} onClick={() => void chats.toggleArchived()}>
        {!chats.archivedLoaded || chats.archivedLoading ? "Archived" : `Archived (${chats.archivedSessions.length})`}
      </button>}
      {!chats.loading && !chats.readError && chats.showArchived && archivedMatches.map(row)}
      {actionError && <p className="library-chats-error" role="alert">{actionError}</p>}
      {chats.error && !chats.readError && <p className="library-chats-error" role="alert">{chats.error}</p>}
    </div>
  </section>;
}
