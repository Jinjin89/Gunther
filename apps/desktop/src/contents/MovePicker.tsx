import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Search, X } from "lucide-react";
import type { ContentsItemRef, ContentsSource } from "@gunther/contracts";
import { isDescendant, rowDetail, sectionList, type ContentsTree } from "./contentsTree";

type PickerProps = { tree: ContentsTree; onClose: () => void } & (
  | { mode: "move"; moving: ContentsItemRef[]; onChoose: (parentId: string | null) => void }
  | { mode: "add"; section: { id: string; title: string }; onAdd: (sourceIds: string[]) => void }
);

/**
 * "Move to…" (pick a section, or Not placed yet) and "Add sources here…" (tick sources to
 * bring into a section). One small dialog; the keyboard-friendly way to arrange.
 */
export function MovePicker(props: PickerProps) {
  const { tree, onClose } = props;
  const [filter, setFilter] = useState("");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => { field.current?.focus(); }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", escape, true);
    return () => window.removeEventListener("keydown", escape, true);
  }, [onClose]);

  const needle = filter.trim().toLowerCase();
  const sections = useMemo(() => sectionList(tree), [tree]);
  const paths = useMemo(() => new Map(sections.map((item) => [item.id, item.path])), [sections]);

  const body = () => {
    if (props.mode === "move") {
      const sectionsMoving = props.moving.filter((ref) => ref.kind === "section");
      const blocked = (id: string) => sectionsMoving.some((ref) => ref.id === id || isDescendant(tree, id, ref.id));
      const onlySources = sectionsMoving.length === 0;
      const rows = sections.filter((item) => !needle || item.title.toLowerCase().includes(needle) || item.path.toLowerCase().includes(needle));
      const top = onlySources ? "Not placed yet" : "Top level";
      return <ul className="contents-picker-list" role="listbox" aria-label="Sections">
        {(!needle || top.toLowerCase().includes(needle)) && <li><button type="button" role="option" aria-selected="false" onClick={() => props.onChoose(null)}>{top}</button></li>}
        {rows.map((item) => <li key={item.id}><button type="button" role="option" aria-selected="false" disabled={blocked(item.id)} style={{ paddingLeft: 10 + 16 * (needle ? 0 : item.depth) }} onClick={() => props.onChoose(item.id)}>{needle ? item.path : item.title}</button></li>)}
        {rows.length === 0 && needle && !top.toLowerCase().includes(needle) && <li className="contents-picker-empty">No section matches.</li>}
      </ul>;
    }
    const placed = [...tree.parents.keys()].flatMap((id) => {
      const source = tree.sections.has(id) ? undefined : findSource(tree, id);
      const parentId = tree.parents.get(id) ?? null;
      return source && parentId !== props.section.id ? [{ source, where: paths.get(parentId ?? "") ?? "" }] : [];
    });
    const all: Array<{ source: ContentsSource; where: string }> = [...tree.unplaced.map((source) => ({ source, where: "" })), ...placed];
    const rows = all.filter((entry) => !needle || entry.source.title.toLowerCase().includes(needle) || entry.where.toLowerCase().includes(needle));
    return <ul className="contents-picker-list" aria-label="Sources">
      {rows.map(({ source, where }) => <li key={source.id}><label>
        <input type="checkbox" checked={chosen.has(source.id)} onChange={() => setChosen((previous) => { const next = new Set(previous); if (!next.delete(source.id)) next.add(source.id); return next; })} />
        <strong>{source.title}</strong><small>{where ? `In ${where}` : "Not placed yet"} · {rowDetail(source)}</small>
      </label></li>)}
      {rows.length === 0 && <li className="contents-picker-empty">{needle ? "No source matches." : "Every source is already here."}</li>}
    </ul>;
  };

  const title = props.mode === "move" ? "Move to…" : `Add sources to “${props.section.title}”`;
  return createPortal(<div className="contents-picker-overlay" role="presentation" onMouseDown={onClose}>
    <section className="contents-picker" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
      <header><strong>{title}</strong><button type="button" className="gx-icon-btn" aria-label="Close" onClick={onClose}><X size={15} /></button></header>
      <label className="contents-picker-search"><Search size={14} /><input ref={field} value={filter} placeholder={props.mode === "move" ? "Filter sections" : "Filter sources"} aria-label="Filter" onChange={(event) => setFilter(event.target.value)} /></label>
      {body()}
      {props.mode === "add" && <footer><button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={chosen.size === 0} onClick={() => props.onAdd([...chosen])}><Check size={13} />Add{chosen.size ? ` ${chosen.size}` : ""}</button></footer>}
    </section>
  </div>, document.body);
}

function findSource(tree: ContentsTree, id: string): ContentsSource | undefined {
  const parent = tree.parents.get(id);
  const node = parent ? tree.sections.get(parent)?.children.find((child) => child.id === id) : undefined;
  return node?.type === "source" ? node.source : undefined;
}
