import { Check, CloudCog, Keyboard, MonitorCog, Moon, PanelRight, RefreshCw, RotateCcw, Sun } from "lucide-react";
import { useState } from "react";
import {
  formatShortcut,
  shortcutFromEvent,
  type ServiceStatus,
  type ShortcutAction,
  type ShortcutMap,
  type ThemeMode,
} from "../shell";

const shortcutRows: Array<{ action: ShortcutAction; label: string; detail: string }> = [
  { action: "openCommand", label: "Open command palette", detail: "Search every view and shell action" },
  { action: "addKnowledge", label: "Add knowledge", detail: "Jump directly to source capture" },
  { action: "openHome", label: "Open Home", detail: "Return to the knowledge cockpit" },
  { action: "openLibrary", label: "Open knowledge library", detail: "Read and edit reusable Knowledge Units" },
  { action: "openMap", label: "Open knowledge map", detail: "Explore entities and associations" },
  { action: "openStudio", label: "Open Story Studio", detail: "Compose knowledge into scenes and artifacts" },
  { action: "openReview", label: "Open review inbox", detail: "Validate proposed knowledge" },
  { action: "toggleInspector", label: "Toggle inspector", detail: "Show or hide workspace context" },
  { action: "refresh", label: "Refresh service", detail: "Reconnect and reload knowledge" },
  { action: "openSettings", label: "Open Settings", detail: "Return to this screen" },
];

interface SettingsPageProps {
  theme: ThemeMode;
  inspectorOpen: boolean;
  serviceStatus: ServiceStatus;
  shortcuts: ShortcutMap;
  onThemeChange: (theme: ThemeMode) => void;
  onInspectorChange: (open: boolean) => void;
  onShortcutChange: (action: ShortcutAction, shortcut: string) => void;
  onResetShortcuts: () => void;
  onReconnect: () => void;
}

export function SettingsPage({
  theme,
  inspectorOpen,
  serviceStatus,
  shortcuts,
  onThemeChange,
  onInspectorChange,
  onShortcutChange,
  onResetShortcuts,
  onReconnect,
}: SettingsPageProps) {
  const [recording, setRecording] = useState<ShortcutAction | null>(null);

  return (
    <div className="page-stack settings-page">
      <section className="page-intro settings-intro">
        <div><span className="eyebrow">Desktop preferences</span><h1>Settings</h1></div>
        <p>Shape the shell around how you capture, explore, and review knowledge.</p>
      </section>

      <section className="settings-section panel">
        <header className="settings-section-header">
          <span className="settings-section-icon"><MonitorCog size={17} /></span>
          <span><strong>Appearance</strong><small>Choose the default desktop presentation.</small></span>
        </header>
        <div className="appearance-grid">
          <button className={theme === "light" ? "is-selected" : ""} onClick={() => onThemeChange("light")}>
            <span className="theme-preview theme-preview-light"><i /><i /><i /></span>
            <span><Sun size={15} /><strong>Light</strong><small>Bright and paper-like</small></span>
            {theme === "light" && <Check size={15} />}
          </button>
          <button className={theme === "dark" ? "is-selected" : ""} onClick={() => onThemeChange("dark")}>
            <span className="theme-preview theme-preview-dark"><i /><i /><i /></span>
            <span><Moon size={15} /><strong>Dark</strong><small>Focused low-light workspace</small></span>
            {theme === "dark" && <Check size={15} />}
          </button>
        </div>
        <label className="setting-toggle-row">
          <span className="setting-row-icon"><PanelRight size={16} /></span>
          <span><strong>Workspace inspector</strong><small>Show growth, review state, and recent sources beside the active view.</small></span>
          <input type="checkbox" checked={inspectorOpen} onChange={(event) => onInspectorChange(event.target.checked)} />
          <i className="toggle-control" />
        </label>
      </section>

      <section className="settings-section panel">
        <header className="settings-section-header">
          <span className="settings-section-icon"><Keyboard size={17} /></span>
          <span><strong>Keyboard shortcuts</strong><small>Click a shortcut, then press a new key combination.</small></span>
          <button className="settings-reset" onClick={onResetShortcuts}><RotateCcw size={13} />Reset defaults</button>
        </header>
        <div className="shortcut-list">
          {shortcutRows.map((row) => (
            <div className="shortcut-row" key={row.action}>
              <span><strong>{row.label}</strong><small>{row.detail}</small></span>
              <button
                className={`shortcut-recorder ${recording === row.action ? "is-recording" : ""}`}
                autoFocus={recording === row.action}
                onClick={() => setRecording(row.action)}
                onBlur={() => setRecording(null)}
                onKeyDown={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (event.key === "Escape") {
                    setRecording(null);
                    return;
                  }
                  const shortcut = shortcutFromEvent(event);
                  if (!shortcut) return;
                  onShortcutChange(row.action, shortcut);
                  setRecording(null);
                }}
              >
                {recording === row.action ? "Press shortcut…" : formatShortcut(shortcuts[row.action])}
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-section panel service-setting">
        <header className="settings-section-header">
          <span className="settings-section-icon"><CloudCog size={17} /></span>
          <span><strong>Knowledge service</strong><small>The Python backend can be developed independently from this shell.</small></span>
          <span className={`service-pill service-${serviceStatus}`}><i />{serviceStatus}</span>
        </header>
        <div className="service-setting-body">
          <p>{serviceStatus === "offline" ? "The desktop is in shell mode. You can still test navigation, themes, profile, and shortcuts." : "Gunther is connected to the local evidence store and extraction service."}</p>
          <button className="button button-secondary" onClick={onReconnect}><RefreshCw size={14} />Reconnect and refresh</button>
        </div>
      </section>
    </div>
  );
}
