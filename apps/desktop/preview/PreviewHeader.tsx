import { ChevronRight, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Sun } from "lucide-react";
import { useLayoutEffect } from "react";
import type { AtlasMode } from "../src/atlas";
import { BrandMark } from "../src/design/BrandMark";

/** One compact application header for reviewing the workspace navigation. */
export function PreviewHeader({ mode, onMode, collapsed, onSidebar, theme, onTheme, onAction }: {
  mode: AtlasMode; onMode: (mode: AtlasMode) => void;
  collapsed: boolean; onSidebar: () => void;
  theme: "light" | "dark"; onTheme: () => void; onAction: () => void;
}) {
  useLayoutEffect(() => { document.documentElement.dataset.libraryWorkspace = "true"; return () => { delete document.documentElement.dataset.libraryWorkspace; }; }, []);
  return <header className="cw-app-header">
    <div className="cw-app-location">
      <button type="button" className="gx-icon-button" aria-label={collapsed ? "Expand Gunther sidebar" : "Collapse Gunther sidebar"} aria-expanded={!collapsed} onClick={onSidebar}>{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button>
      <button type="button" className="cw-app-brand" aria-label="Gunther home" onClick={() => onMode("contents")}><BrandMark size={19} /><span>Gunther</span></button>
      <span className="cw-app-location-divider" />
      <button type="button" className="cw-app-library" onClick={() => onMode("contents")}><span className="cw-app-library-parent">Libraries <ChevronRight size={12} /></span><strong>Leadership</strong></button>
    </div>
    <nav className="cw-app-tabs" aria-label="Library views">{([{ id: "ask", label: "Ask" }, { id: "contents", label: "Contents" }, { id: "outputs", label: "Artifacts" }] as const).map((item) => <button type="button" key={item.id} aria-current={mode === item.id ? "page" : undefined} onClick={() => onMode(item.id)}>{item.label}</button>)}</nav>
    <div className="cw-app-actions"><span className="cw-preview-badge">Design preview</span><button type="button" className="gx-icon-button" aria-label="Search" onClick={onAction}><Search size={15} /></button><button type="button" className="gx-icon-button" aria-label="Toggle theme" onClick={onTheme}>{theme === "light" ? <Moon size={15} /> : <Sun size={15} />}</button><button type="button" className="gx-btn gx-btn-primary gx-btn-sm" aria-label="Capture" onClick={onAction}><Plus size={14} /><span>Capture</span></button></div>
  </header>;
}
