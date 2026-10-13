import { MoreHorizontal } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MenuItem } from "../contents/SectionMenu";

/** Artifact menus escape the resizable preview's scrolling and clipping boundaries. */
export function ArtifactMenu({ label, items, trigger, triggerLabel, triggerClass = "gx-icon-button" }: { label: string; items: MenuItem[]; trigger?: ReactNode; triggerLabel?: string; triggerClass?: string }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0, maxHeight: 400 });
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const close = () => { setOpen(false); button.current?.focus(); };
  useLayoutEffect(() => {
    if (!open || !button.current || !menu.current) return;
    const anchor = button.current.getBoundingClientRect();
    const height = Math.min(menu.current.scrollHeight, window.innerHeight - 16);
    const top = anchor.bottom + height + 6 <= window.innerHeight ? anchor.bottom + 5 : Math.max(8, anchor.top - height - 5);
    setPosition({ left: Math.max(8, Math.min(window.innerWidth - 208, anchor.right - 200)), top, maxHeight: window.innerHeight - top - 8 });
    menu.current.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!button.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); close(); } };
    const resized = () => setOpen(false);
    const scrolled = (event: Event) => { if (!menu.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape, true); window.addEventListener("resize", resized); document.addEventListener("scroll", scrolled, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape, true); window.removeEventListener("resize", resized); document.removeEventListener("scroll", scrolled, true); };
  }, [open]);
  return <span className="contents-menu"><button ref={button} type="button" className={trigger ? triggerClass : "contents-icon-btn"} aria-label={triggerLabel ?? label} title={triggerLabel ?? label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>{trigger ?? <MoreHorizontal size={15} />}</button>{open && createPortal(<div ref={menu} className="contents-menu-list artifact-portal-menu" role="menu" aria-label={label} style={{ position: "fixed", left: position.left, right: "auto", top: position.top, width: 200, maxHeight: position.maxHeight, overflowY: "auto", zIndex: 500 }} onKeyDown={(event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault(); const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")]; const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  }}>{items.map((item) => { const Icon = item.icon; return <button key={item.label} type="button" role="menuitem" disabled={item.disabled} data-divider={item.divider || undefined} className={item.danger ? "is-danger" : ""} onClick={() => { close(); item.onSelect(); }}>{Icon && <Icon size={14} />}{item.label}</button>; })}</div>, document.body)}</span>;
}
