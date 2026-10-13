import { Download, MoreHorizontal, Settings2 } from "lucide-react";
import { normalizeAtlasMode, type AtlasMode } from "../atlas";
import { PopMenu } from "../contents/SectionMenu";

const views: Array<{ id: AtlasMode; label: string }> = [
  { id: "ask", label: "Ask" },
  { id: "contents", label: "Contents" },
  { id: "outputs", label: "Artifacts" },
];

/** Library navigation shared by the application titlebar and standalone workspaces. */
export function LibraryWorkspaceNavigation({ mode, onMode, onEdit, onExport }: {
  mode: AtlasMode;
  onMode: (mode: AtlasMode) => void;
  onEdit: () => void;
  onExport: () => void;
}) {
  const current = normalizeAtlasMode(mode);
  return <div className="gx-library-navigation" data-no-drag>
    <nav className="gx-library-tabs" aria-label="Library views">
      {views.map((view) => <button key={view.id} type="button" aria-current={current === view.id ? "page" : undefined} onClick={() => onMode(view.id)}>{view.label}</button>)}
    </nav>
    <PopMenu label="Library actions" triggerLabel="Library actions" triggerClass="gx-icon-button" trigger={<MoreHorizontal size={15} />} items={[
      { label: "Edit library details", icon: Settings2, onSelect: onEdit },
      { label: "Export library", icon: Download, onSelect: onExport },
    ]} />
  </div>;
}
