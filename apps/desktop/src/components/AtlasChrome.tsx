import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { BookOpen, Home, Inbox, Moon, Plus, Search, Settings, Sun } from "lucide-react";

export type AtlasView = "home" | "library" | "base" | "notebook" | "inbox" | "search" | "settings" | "account";

interface AtlasTitlebarProps {
  path: string;
  theme: "light" | "dark";
  onSearch: () => void;
  onCapture: () => void;
  onTheme: () => void;
}

export function AtlasTitlebar({ path, theme, onSearch, onCapture, onTheme }: AtlasTitlebarProps) {
  const startWindowDrag = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button !== 0 || !("__TAURI_INTERNALS__" in window)) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select, textarea, [data-no-drag]")) return;
    event.preventDefault();
    void getCurrentWindow().startDragging();
  };

  return (
    <header className="atlas-titlebar" data-tauri-drag-region onMouseDown={startWindowDrag}>
      <div className="atlas-traffic-space" data-tauri-drag-region />
      <div className="atlas-window-path" data-tauri-drag-region>
        <strong data-tauri-drag-region>Gunther</strong>
        <span data-tauri-drag-region>/</span>
        <span data-tauri-drag-region>{path}</span>
      </div>
      <div className="atlas-titlebar-drag-fill" data-tauri-drag-region />
      <button className="atlas-search-trigger" onClick={onSearch} data-no-drag>
        <Search size={13} />
        <span>Search your knowledge</span>
        <kbd>⌘K</kbd>
      </button>
      <span className="atlas-save-state" data-tauri-drag-region><i />Saved locally</span>
      <button className="atlas-title-icon" onClick={onTheme} aria-label="Toggle theme" data-no-drag>
        {theme === "light" ? <Moon size={15} /> : <Sun size={15} />}
      </button>
      <button className="atlas-add-button" onClick={onCapture} data-no-drag><Plus size={14} />Capture</button>
    </header>
  );
}

interface AtlasRailProps {
  active: AtlasView;
  inboxCount: number;
  onNavigate: (view: AtlasView) => void;
}

export function AtlasRail({ active, inboxCount, onNavigate }: AtlasRailProps) {
  return (
    <aside className="atlas-rail" aria-label="Primary navigation">
      <button className="atlas-brand" onClick={() => onNavigate("home")} aria-label="Gunther home">G</button>
      <nav>
        <button className={active === "home" ? "is-active" : ""} onClick={() => onNavigate("home")} aria-label="Home" aria-current={active === "home" ? "page" : undefined} title="Home">
          <Home size={18} /><span>Home</span>
        </button>
        <button className={active === "library" || active === "base" ? "is-active" : ""} onClick={() => onNavigate("library")} aria-label="Library" aria-current={active === "library" || active === "base" ? "page" : undefined} title="Library">
          <BookOpen size={18} /><span>Libraries</span>
        </button>
        <button className={active === "inbox" ? "is-active" : ""} onClick={() => onNavigate("inbox")} aria-label="Inbox" aria-current={active === "inbox" ? "page" : undefined} title="Inbox">
          <Inbox size={18} /><span>Inbox</span>{inboxCount > 0 && <i>{inboxCount}</i>}
        </button>
      </nav>
      <div className="atlas-rail-spacer" />
      <button className={active === "search" ? "is-active" : ""} onClick={() => onNavigate("search")} aria-label="Search" aria-current={active === "search" ? "page" : undefined} title="Search"><Search size={18} /><span>Search</span></button>
      <button className={active === "settings" ? "is-active" : ""} onClick={() => onNavigate("settings")} aria-label="Settings" aria-current={active === "settings" ? "page" : undefined} title="Settings"><Settings size={18} /><span>Settings</span></button>
      <button className={`atlas-avatar ${active === "account" ? "is-active" : ""}`} onClick={() => onNavigate("account")} aria-label="Account" aria-current={active === "account" ? "page" : undefined} title="Account">KE</button>
    </aside>
  );
}

export function AtlasPage({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <main className={`atlas-page ${className}`}>{children}</main>;
}
