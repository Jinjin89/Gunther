import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Moon, PanelRightClose, PanelRightOpen, Plus, RefreshCw, Search, Sun } from "lucide-react";
import { formatShortcut, type ServiceStatus, type ThemeMode } from "../shell";
import { workspaceViewLabels, type WorkspaceView } from "./NavigationRail";

interface WorkspaceTitlebarProps {
  view: WorkspaceView;
  serviceStatus: ServiceStatus;
  theme: ThemeMode;
  inspectorOpen: boolean;
  commandShortcut: string;
  onAddKnowledge: () => void;
  onOpenCommand: () => void;
  onToggleTheme: () => void;
  onToggleInspector: () => void;
  onReconnect: () => void;
}

export function WorkspaceTitlebar({
  view,
  serviceStatus,
  theme,
  inspectorOpen,
  commandShortcut,
  onAddKnowledge,
  onOpenCommand,
  onToggleTheme,
  onToggleInspector,
  onReconnect,
}: WorkspaceTitlebarProps) {
  const statusLabel = serviceStatus === "online" ? "Saved locally" : serviceStatus === "connecting" ? "Connecting…" : "Shell mode";

  const startWindowDrag = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button !== 0 || !("__TAURI_INTERNALS__" in window)) return;

    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select, textarea, [data-no-drag]")) return;

    event.preventDefault();
    void getCurrentWindow().startDragging();
  };

  return (
    <header
      className="workspace-titlebar"
      data-tauri-drag-region
      onMouseDown={startWindowDrag}
    >
      <div className="traffic-light-space" data-tauri-drag-region />
      <div className="titlebar-drag-zone" data-tauri-drag-region>
        <span className="wordmark" data-tauri-drag-region>Gunther</span>
        <div className="titlebar-path" data-tauri-drag-region>
          <span data-tauri-drag-region>/</span>
          <strong data-tauri-drag-region>{workspaceViewLabels[view]}</strong>
        </div>
      </div>
      <button className="titlebar-command" onClick={onOpenCommand} aria-label="Open command palette">
        <Search size={13} /><span>Search or jump</span><kbd>{formatShortcut(commandShortcut)}</kbd>
      </button>
      <span className={`save-state status-${serviceStatus}`}><i />{statusLabel}</span>
      <button className={`titlebar-icon ${serviceStatus === "connecting" ? "is-spinning" : ""}`} onClick={onReconnect} aria-label="Refresh knowledge" title="Refresh knowledge">
        <RefreshCw size={14} />
      </button>
      <button className="titlebar-icon" onClick={onToggleTheme} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`} title={`Switch to ${theme === "light" ? "dark" : "light"} theme`}>
        {theme === "light" ? <Moon size={14} /> : <Sun size={14} />}
      </button>
      <button className="titlebar-icon inspector-toggle" onClick={onToggleInspector} aria-label={`${inspectorOpen ? "Hide" : "Show"} workspace inspector`} title={`${inspectorOpen ? "Hide" : "Show"} workspace inspector (⌘\\)`}>
        {inspectorOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
      </button>
      <button className="titlebar-add" onClick={onAddKnowledge}>
        <Plus size={14} />Add knowledge
      </button>
    </header>
  );
}
