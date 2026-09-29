import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { ChevronRight, Home, Inbox, Library, Moon, Plus, Search, Settings, Sun, Trash2, UserRound } from "lucide-react";
import type { KnowledgeBase } from "../atlas";
import { BrandMark } from "../design/BrandMark";
import { LibraryGlyph } from "../design/LibraryGlyph";

export type AtlasView = "home" | "library" | "base" | "notebook" | "inbox" | "settings" | "account" | "item" | "trash";

interface BreadcrumbSegment {
  label: string;
  onClick?: () => void;
}

interface AtlasTitlebarProps {
  path: BreadcrumbSegment[];
  theme: "light" | "dark";
  serviceOnline?: boolean;
  showSearch?: boolean;
  onHome: () => void;
  onSearch: () => void;
  onCapture: () => void;
  onTheme: () => void;
}

export function AtlasTitlebar({ path, theme, serviceOnline = true, showSearch = true, onHome, onSearch, onCapture, onTheme }: AtlasTitlebarProps) {
  const startWindowDrag = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button !== 0 || !("__TAURI_INTERNALS__" in window)) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select, textarea, [data-no-drag]")) return;
    event.preventDefault();
    void getCurrentWindow().startDragging();
  };

  return (
    <header className="gx-titlebar" data-tauri-drag-region onMouseDown={startWindowDrag}>
      <div className="gx-titlebar-lead" data-tauri-drag-region>
        <span className="gx-traffic-space" data-tauri-drag-region />
        <button type="button" className="gx-wordmark" onClick={onHome} aria-label="Gunther home" data-no-drag>
          <BrandMark size={18} />
          <span>Gunther</span>
        </button>
      </div>
      <nav className="gx-breadcrumb" aria-label="Location" data-tauri-drag-region>
        {path.map((segment, index) => (
          <span className="gx-crumb" key={`${segment.label}-${index}`} data-tauri-drag-region>
            {index > 0 && <ChevronRight size={13} className="gx-crumb-separator" aria-hidden="true" />}
            {segment.onClick
              ? <button type="button" onClick={segment.onClick} data-no-drag>{segment.label}</button>
              : <span aria-current={index === path.length - 1 ? "page" : undefined} data-tauri-drag-region>{segment.label}</span>}
          </span>
        ))}
      </nav>
      <div className="gx-titlebar-fill" data-tauri-drag-region />
      {showSearch && (
        <button type="button" className="gx-search-trigger" onClick={onSearch} data-no-drag>
          <Search size={14} />
          <span>Search</span>
          <kbd>⌘K</kbd>
        </button>
      )}
      <span
        className={`gx-save-state ${serviceOnline ? "" : "is-offline"}`}
        role="status"
        data-tauri-drag-region
        title={serviceOnline ? "Everything is stored on this device" : "The local knowledge service is not responding. New captures stay on this device and are sent when it returns."}
      >
        <i />{serviceOnline ? "Saved locally" : "Reconnecting…"}
      </span>
      <button type="button" className="gx-icon-button" onClick={onTheme} aria-label="Toggle theme" title={theme === "light" ? "Dark appearance" : "Light appearance"} data-no-drag>
        <span className="gx-theme-icon" key={theme}>{theme === "light" ? <Moon size={15} /> : <Sun size={15} />}</span>
      </button>
      <button type="button" className="gx-btn gx-btn-primary gx-btn-sm gx-capture-button" onClick={onCapture} data-no-drag>
        <Plus size={14} className="gx-capture-plus" />Capture
      </button>
    </header>
  );
}

interface AtlasRailProps {
  active: AtlasView;
  inboxCount: number;
  bases?: KnowledgeBase[];
  basesReady?: boolean;
  activeBaseId?: string | null;
  profileName?: string;
  onNavigate: (view: AtlasView) => void;
  onOpenBase?: (id: string) => void;
  onCreateBase?: () => void;
}

const MAX_SIDEBAR_LIBRARIES = 7;

const initialsOf = (name: string) => name.trim().split(/\s+/).slice(0, 2).map((part) => part[0] ?? "").join("").toUpperCase() || "G";

export function AtlasRail({ active, inboxCount, bases = [], basesReady = true, activeBaseId = null, profileName = "", onNavigate, onOpenBase, onCreateBase }: AtlasRailProps) {
  const item = (view: AtlasView, label: string, icon: ReactNode, trailing?: ReactNode, isActive = active === view, describedBy?: string) => (
    <button
      type="button"
      className={`gx-nav-item ${isActive ? "is-active" : ""}`}
      onClick={() => onNavigate(view)}
      aria-label={label}
      aria-describedby={describedBy}
      aria-current={isActive ? "page" : undefined}
      title={label}
    >
      <span className="gx-nav-icon">{icon}</span>
      <span className="gx-nav-label">{label}</span>
      {trailing}
    </button>
  );
  const visibleBases = bases.slice(0, MAX_SIDEBAR_LIBRARIES);
  const displayName = profileName.trim() || "Your workspace";

  return (
    <aside className="gx-sidebar" aria-label="Primary navigation">
      <nav className="gx-nav" aria-label="Main">
        {item("home", "Home", <Home size={16} />)}
        {item("library", "Libraries", <Library size={16} />, undefined, active === "library")}
        {item("inbox", "Inbox", <Inbox size={16} />, inboxCount > 0 ? <i className="gx-nav-count" id="gx-inbox-count" title={`${inboxCount} waiting`}>{inboxCount}</i> : undefined, active === "inbox", inboxCount > 0 ? "gx-inbox-count" : undefined)}
      </nav>

      {onOpenBase && (
        <section className="gx-sidebar-section" aria-label="Your libraries">
          <header>
            <span>Libraries</span>
            <span className="gx-sidebar-section-actions">
              {onCreateBase && <button type="button" onClick={onCreateBase} aria-label="New library" title="New library"><Plus size={13} /></button>}
              <button type="button" onClick={() => onNavigate("library")} aria-label="Show all libraries" title="Show all libraries"><ChevronRight size={13} /></button>
            </span>
          </header>
          <div className="gx-sidebar-libraries">
            {visibleBases.map((base) => (
              <button
                type="button"
                key={base.id}
                className={`gx-sidebar-library ${active === "base" && activeBaseId === base.id ? "is-active" : ""}`}
                onClick={() => onOpenBase(base.id)}
                aria-current={active === "base" && activeBaseId === base.id ? "page" : undefined}
                title={base.title}
              >
                <LibraryGlyph base={base} size="xs" />
                <span>{base.title}</span>
              </button>
            ))}
            {!basesReady && [0, 1, 2].map((index) => <span className="gx-sidebar-skeleton" key={index} aria-hidden="true"><i /><b /></span>)}
            {basesReady && bases.length === 0 && <p className="gx-sidebar-empty">Libraries you create appear here.</p>}
            {bases.length > MAX_SIDEBAR_LIBRARIES && (
              <button type="button" className="gx-sidebar-more" onClick={() => onNavigate("library")}>
                {bases.length - MAX_SIDEBAR_LIBRARIES} more
              </button>
            )}
          </div>
        </section>
      )}

      <div className="gx-sidebar-spacer" />
      <nav className="gx-nav gx-nav-footer" aria-label="Workspace">
        {item("trash", "Trash", <Trash2 size={16} />)}
        {item("settings", "Settings", <Settings size={16} />)}
        <button
          type="button"
          className={`gx-nav-item gx-account ${active === "account" ? "is-active" : ""}`}
          onClick={() => onNavigate("account")}
          aria-current={active === "account" ? "page" : undefined}
          aria-label="Account"
          title="Account"
        >
          <span className="gx-avatar" aria-hidden="true">{profileName.trim() ? initialsOf(profileName) : <UserRound size={13} />}</span>
          <span className="gx-nav-label">{displayName}</span>
        </button>
      </nav>
    </aside>
  );
}

export function AtlasPage({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`atlas-page gx-page ${className}`}>{children}</div>;
}
