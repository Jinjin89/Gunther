import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDownAZ, ArrowUpRight, FolderInput, FolderOutput, FolderPlus, MessageSquareText, MoreHorizontal, PenLine, Plus, Trash2, type LucideIcon } from "lucide-react";

export interface MenuItem {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** A thin line above this item. */
  divider?: boolean;
}

/** A small popover menu opened by a button; closes on Esc, outside click and choosing. */
export function PopMenu({ label, items, trigger, triggerLabel, triggerClass = "gx-btn gx-btn-quiet gx-btn-sm", align = "end", className = "" }: { label: string; items: MenuItem[]; trigger?: ReactNode; triggerLabel?: string; triggerClass?: string; align?: "start" | "end"; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape, true); };
  }, [open]);
  return <span className={`contents-menu ${className}`} ref={root} onDoubleClick={(event) => event.stopPropagation()}>
    <button type="button" className={trigger ? triggerClass : "contents-icon-btn"} aria-label={trigger ? triggerLabel : label} title={trigger ? triggerLabel : undefined} aria-haspopup="menu" aria-expanded={open} onClick={(event) => { event.stopPropagation(); setOpen((value) => !value); }}>{trigger ?? <MoreHorizontal size={15} />}</button>
    {open && <div className={`contents-menu-list is-${align}`} role="menu" aria-label={label}>
      {items.map((item) => {
        const Icon = item.icon;
        return <button key={item.label} type="button" role="menuitem" className={item.danger ? "is-danger" : ""} disabled={item.disabled} data-divider={item.divider || undefined} onClick={(event) => { event.stopPropagation(); setOpen(false); item.onSelect(); }}>{Icon && <Icon size={14} />}{item.label}</button>;
      })}
    </div>}
  </span>;
}

export interface SectionActions {
  onAddSubsection: () => void;
  onRename: () => void;
  onAddSources: () => void;
  onSort: () => void;
  onMove: () => void;
  onAsk: () => void;
  onDelete: () => void;
}

/** The ⋯ menu of a section heading. */
export function SectionMenu({ title, actions, canAsk }: { title: string; actions: SectionActions; canAsk: boolean }) {
  return <PopMenu label={`Actions for ${title}`} items={[
    { label: "Rename", icon: PenLine, onSelect: actions.onRename },
    { label: "Add sub-section", icon: FolderPlus, onSelect: actions.onAddSubsection },
    { label: "Add sources here…", icon: Plus, onSelect: actions.onAddSources },
    { label: "Sort by name", icon: ArrowDownAZ, onSelect: actions.onSort },
    { label: "Move to…", icon: FolderInput, onSelect: actions.onMove },
    { label: "Ask this section", icon: MessageSquareText, onSelect: actions.onAsk, disabled: !canAsk, divider: true },
    { label: "Delete section", icon: Trash2, onSelect: actions.onDelete, danger: true, divider: true },
  ]} />;
}

/** The ⋯ menu of a source row. */
export function SourceMenu({ title, placed, onOpen, onRename, onMove, onTakeOut }: { title: string; placed: boolean; onOpen: () => void; onRename: () => void; onMove: () => void; onTakeOut: () => void }) {
  return <PopMenu label={`Actions for ${title}`} items={[
    { label: "Open", icon: ArrowUpRight, onSelect: onOpen },
    { label: "Rename", icon: PenLine, onSelect: onRename },
    { label: "Move to…", icon: FolderInput, onSelect: onMove, divider: true },
    ...(placed ? [{ label: "Take out of section", icon: FolderOutput, onSelect: onTakeOut }] : []),
  ]} />;
}
