import type { LibraryContents } from "@gunther/contracts";
import { ChevronRight, FileText, FolderOpen } from "lucide-react";
import { useMemo, useRef, useEffect, useState, type CSSProperties } from "react";
import { buildTree, rowDetail, sourcesIn, visibleRows } from "../contents/contentsTree";

/** A native checkbox also conveys a partially selected course or section. */
function SelectionBox({ selected, total, title, onChange }: { selected: number; total: number; title: string; onChange: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const mixed = selected > 0 && selected < total;
  useEffect(() => { if (input.current) input.current.indeterminate = mixed; }, [mixed]);
  return <input ref={input} type="checkbox" checked={total > 0 && selected === total} disabled={total === 0} aria-label={title} aria-checked={mixed ? "mixed" : selected === total && total > 0} onChange={onChange} />;
}

/** Reuse Contents' hierarchy, source placements, filtering and order when choosing material. */
export function SourceScopeTree({ contents, filter, picked, onChoose }: {
  contents: LibraryContents;
  filter: string;
  picked: ReadonlySet<string>;
  onChoose: (ids: string[], select: boolean) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(contents), [contents]);
  const rows = useMemo(() => visibleRows(tree, collapsed, filter), [tree, collapsed, filter]);
  // Collapse only changes what is on screen, never what Select all includes.
  const matchingIds = useMemo(() => new Set(visibleRows(tree, new Set(), filter).filter((row) => row.type === "source").map((row) => row.id)), [tree, filter]);
  const selected = (ids: string[]) => ids.filter((id) => picked.has(id)).length;
  const matching = [...matchingIds];
  const all = matching.length > 0 && selected(matching) === matching.length;
  const toggle = (id: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (!next.delete(id)) next.add(id);
    return next;
  });

  return <fieldset className="outputs-picker-group outputs-source-group">
    <legend>Sources</legend>
    <header>
      <span>{matching.length ? `${selected(matching)} of ${matching.length} chosen` : contents.sources.length ? "No matching sources" : "No sources"}</span>
      {matching.length > 0 && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChoose(matching, !all)}>{all ? "Clear" : "Select all"}</button>}
    </header>
    <p className="outputs-source-hint">Select a section to include its sources and sub-sections.</p>
    <ul className="outputs-source-tree" aria-label="Library contents">
      {rows.map((row) => {
        const style = { "--scope-depth": Math.min(row.depth, 8) } as CSSProperties;
        if (row.type === "empty") return <li key={row.key} className="outputs-source-empty" style={style}>No sources in this section</li>;
        if (row.type === "source" && row.source) return <li key={row.key} style={style}>
          <label className="outputs-source-row">
            <input type="checkbox" checked={picked.has(row.id)} onChange={() => onChoose([row.id], !picked.has(row.id))} />
            <FileText size={14} aria-hidden="true" />
            <span><strong title={row.source.title}>{row.source.title}</strong><small>{[row.source.kind, rowDetail(row.source)].filter(Boolean).join(" · ")}</small></span>
          </label>
        </li>;
        const ids = (row.type === "unplaced" ? tree.unplaced : row.node ? sourcesIn(row.node) : []).map((source) => source.id).filter((id) => matchingIds.has(id));
        const count = selected(ids);
        const title = row.type === "unplaced" ? "Not placed yet" : row.node?.type === "section" ? row.node.section.title : "";
        return <li key={row.key} style={style}>
          <div className="outputs-source-section">
            {row.type === "section" ? <button type="button" className="outputs-source-expand" aria-label={`${row.collapsed ? "Expand" : "Collapse"} ${title}`} aria-expanded={!row.collapsed} disabled={Boolean(filter.trim())} onClick={() => toggle(row.id)}><ChevronRight size={14} /></button> : <span className="outputs-source-expand" />}
            <label>
              <SelectionBox selected={count} total={ids.length} title={`Select ${title}`} onChange={() => onChoose(ids, count !== ids.length)} />
              <FolderOpen size={14} aria-hidden="true" />
              <strong title={title}>{title}</strong>
              <small>{count > 0 ? `${count} / ${ids.length}` : `${ids.length} ${ids.length === 1 ? "source" : "sources"}`}</small>
            </label>
          </div>
        </li>;
      })}
    </ul>
  </fieldset>;
}
