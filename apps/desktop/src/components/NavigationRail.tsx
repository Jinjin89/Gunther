import { Inbox, LibraryBig, Network, Plus, RadioTower, Search, Settings2, Presentation } from "lucide-react";

export type WorkspaceView = "overview" | "library" | "map" | "studio" | "review" | "settings" | "account";

export const workspaceNavigation = [
  { id: "overview" as const, label: "Home", icon: RadioTower },
  { id: "library" as const, label: "Knowledge library", icon: LibraryBig },
  { id: "map" as const, label: "Knowledge map", icon: Network },
  { id: "studio" as const, label: "Story studio", icon: Presentation },
  { id: "review" as const, label: "Review inbox", icon: Inbox },
];

export const workspaceViewLabels: Record<WorkspaceView, string> = {
  overview: "Home",
  library: "Knowledge library",
  map: "Knowledge map",
  studio: "Story studio",
  review: "Review inbox",
  settings: "Settings",
  account: "Profile",
};

interface NavigationRailProps {
  active: WorkspaceView;
  provisionalCount: number;
  profileName: string;
  commandShortcut: string;
  settingsShortcut: string;
  onNavigate: (view: WorkspaceView) => void;
  onOpenCommand: () => void;
  onAddKnowledge: () => void;
}

export function NavigationRail({ active, provisionalCount, profileName, commandShortcut, settingsShortcut, onNavigate, onOpenCommand, onAddKnowledge }: NavigationRailProps) {
  const initials = profileName.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "G";

  return (
    <aside className="navigation-rail" aria-label="Workspace navigation">
      <button className="rail-brand" onClick={() => onNavigate("overview")} aria-label="Gunther home">
        <span>G</span>
      </button>

      <nav>
        {workspaceNavigation.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={active === id ? "is-active" : ""}
            onClick={() => onNavigate(id)}
            aria-label={label}
            title={label}
          >
            <Icon size={19} strokeWidth={1.65} />
            {id === "review" && provisionalCount > 0 && <i>{provisionalCount > 99 ? "99+" : provisionalCount}</i>}
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="rail-spacer" />
      <button className="rail-add" onClick={onAddKnowledge} aria-label="Add knowledge" title="Add knowledge">
        <Plus size={19} strokeWidth={1.8} />
        <span>Add knowledge</span>
      </button>
      <div className="rail-divider" />
      <button className="rail-command" onClick={onOpenCommand} aria-label="Open command palette" title={`Command palette (${commandShortcut})`}>
        <Search size={18} strokeWidth={1.7} />
        <span>Commands</span>
      </button>
      <button className={`rail-settings ${active === "settings" ? "is-active" : ""}`} onClick={() => onNavigate("settings")} aria-label="Settings" title={`Settings (${settingsShortcut})`}>
        <Settings2 size={18} strokeWidth={1.7} />
        <span>Settings</span>
      </button>
      <button className={`rail-profile ${active === "account" ? "is-active" : ""}`} onClick={() => onNavigate("account")} aria-label="User profile" title="User profile">
        <span>{initials}</span>
      </button>
    </aside>
  );
}
