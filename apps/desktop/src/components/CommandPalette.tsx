import { CornerDownLeft, Moon, PanelRight, Plus, RefreshCw, Search, Settings2, Sun, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatShortcut, type ServiceStatus, type ShortcutMap, type ThemeMode } from "../shell";
import { workspaceNavigation, type WorkspaceView } from "./NavigationRail";

interface CommandPaletteProps {
  open: boolean;
  activeView: WorkspaceView;
  theme: ThemeMode;
  inspectorOpen: boolean;
  serviceStatus: ServiceStatus;
  shortcuts: ShortcutMap;
  onClose: () => void;
  onNavigate: (view: WorkspaceView) => void;
  onAddKnowledge: () => void;
  onToggleTheme: () => void;
  onToggleInspector: () => void;
  onReconnect: () => void;
}

interface CommandItem {
  id: string;
  label: string;
  detail: string;
  shortcut?: string;
  icon: typeof Search;
  action: () => void;
}

export function CommandPalette({
  open,
  activeView,
  theme,
  inspectorOpen,
  serviceStatus,
  shortcuts,
  onClose,
  onNavigate,
  onAddKnowledge,
  onToggleTheme,
  onToggleInspector,
  onReconnect,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const run = (action: () => void) => {
    action();
    onClose();
  };

  const commands = useMemo<CommandItem[]>(() => [
    ...workspaceNavigation.map(({ id, label, icon }, index) => ({
      id: `view-${id}`,
      label: `Open ${label}`,
      detail: activeView === id ? "Current view" : "Navigate workspace",
      shortcut: formatShortcut(shortcuts[["openHome", "openLibrary", "openMap", "openStudio", "openReview"][index] as keyof ShortcutMap]),
      icon,
      action: () => onNavigate(id),
    })),
    { id: "settings", label: "Open Settings", detail: "Appearance, service, and shortcuts", shortcut: formatShortcut(shortcuts.openSettings), icon: Settings2, action: () => onNavigate("settings") },
    { id: "profile", label: "Open user profile", detail: "Edit the local desktop identity", icon: UserRound, action: () => onNavigate("account") },
    { id: "add", label: "Add knowledge", detail: "Capture a new source", shortcut: formatShortcut(shortcuts.addKnowledge), icon: Plus, action: onAddKnowledge },
    {
      id: "inspector",
      label: inspectorOpen ? "Hide workspace inspector" : "Show workspace inspector",
      detail: "Toggle the context sidebar",
      shortcut: formatShortcut(shortcuts.toggleInspector),
      icon: PanelRight,
      action: onToggleInspector,
    },
    {
      id: "theme",
      label: theme === "light" ? "Switch to dark theme" : "Switch to light theme",
      detail: "Change desktop appearance",
      icon: theme === "light" ? Moon : Sun,
      action: onToggleTheme,
    },
    {
      id: "reconnect",
      label: serviceStatus === "online" ? "Refresh knowledge" : "Reconnect knowledge service",
      detail: serviceStatus === "offline" ? "The shell is currently working offline" : "Reload data from the Python service",
      shortcut: formatShortcut(shortcuts.refresh),
      icon: RefreshCw,
      action: onReconnect,
    },
  ], [activeView, inspectorOpen, onAddKnowledge, onNavigate, onReconnect, onToggleInspector, onToggleTheme, serviceStatus, shortcuts, theme]);

  const normalized = query.trim().toLowerCase();
  const filtered = normalized
    ? commands.filter((command) => `${command.label} ${command.detail}`.toLowerCase().includes(normalized))
    : commands;

  if (!open) return null;

  return (
    <div className="command-overlay" role="presentation" onMouseDown={onClose}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(event) => event.stopPropagation()}>
        <label className="command-search">
          <Search size={17} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
              if (event.key === "Enter" && filtered[0]) run(filtered[0].action);
            }}
            placeholder="Search views and commands…"
          />
          <kbd>esc</kbd>
        </label>
        <div className="command-results">
          <span className="command-group-label">Workspace commands</span>
          {filtered.map((command, index) => {
            const Icon = command.icon;
            return (
              <button key={command.id} className={index === 0 ? "is-first" : ""} onClick={() => run(command.action)}>
                <span className="command-icon"><Icon size={16} strokeWidth={1.7} /></span>
                <span><strong>{command.label}</strong><small>{command.detail}</small></span>
                {command.shortcut && <kbd>{command.shortcut}</kbd>}
                {index === 0 && !command.shortcut && <CornerDownLeft size={13} />}
              </button>
            );
          })}
          {filtered.length === 0 && <p className="command-empty">No matching command.</p>}
        </div>
        <footer><span><kbd>↵</kbd> Run first</span><span><kbd>{formatShortcut(shortcuts.openCommand)}</kbd> Toggle palette</span></footer>
      </section>
    </div>
  );
}
