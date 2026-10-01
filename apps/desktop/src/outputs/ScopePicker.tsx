import type { KnowledgeSessionSummary, KnowledgeUnit, OutputScope, SourceSummary } from "@gunther/contracts";
import { CircleAlert, LoaderCircle, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { knowledgeApi } from "../api";
import { useEscape } from "../shortcuts/shortcuts";

export interface ScopeCounts {
  sources: number;
  units: number;
  sessions: number;
}

const plural = (count: number, one: string) => `${count} ${count === 1 ? one : `${one}s`}`;

/** "12 sources · 3 saved · 1 discussion": what an output is built from, in words. */
export function describeScope({ sources, units, sessions }: ScopeCounts): string {
  const parts = [
    sources > 0 ? plural(sources, "source") : "",
    units > 0 ? `${units} saved` : "",
    sessions > 0 ? plural(sessions, "discussion") : "",
  ].filter(Boolean);
  return parts.join(" · ") || "Nothing chosen";
}

export const scopeCounts = (scope: OutputScope): ScopeCounts => ({
  sources: scope.sourceIds.length,
  units: scope.unitIds.length,
  sessions: scope.sessionIds.length,
});

interface Row {
  id: string;
  title: string;
  meta: string;
}

function Group({ title, rows, picked, onToggle, onAll, empty }: {
  title: string;
  rows: Row[];
  picked: Set<string>;
  onToggle: (id: string) => void;
  onAll: (select: boolean) => void;
  empty: string;
}) {
  const all = rows.length > 0 && rows.every((row) => picked.has(row.id));
  return <fieldset className="outputs-picker-group">
    <legend>{title}</legend>
    <header>
      <span>{rows.length === 0 ? empty : `${rows.filter((row) => picked.has(row.id)).length} of ${rows.length} chosen`}</span>
      {rows.length > 0 && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onAll(!all)}>{all ? "Clear" : "Select all"}</button>}
    </header>
    <ul>
      {rows.map((row) => <li key={row.id}>
        <label>
          <input type="checkbox" checked={picked.has(row.id)} onChange={() => onToggle(row.id)} />
          <span><strong>{row.title}</strong>{row.meta && <small>{row.meta}</small>}</span>
        </label>
      </li>)}
    </ul>
  </fieldset>;
}

/** Choose what an output is built from: sources, saved knowledge, and Ask discussions. */
export function ScopePicker({ baseId, initial, onApply, onClose }: {
  baseId: string;
  initial: OutputScope;
  onApply: (scope: OutputScope) => void;
  onClose: () => void;
}) {
  const [sources, setSources] = useState<SourceSummary[] | null>(null);
  const [units, setUnits] = useState<KnowledgeUnit[]>([]);
  const [sessions, setSessions] = useState<KnowledgeSessionSummary[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [pickedSources, setPickedSources] = useState(() => new Set(initial.sourceIds));
  const [pickedUnits, setPickedUnits] = useState(() => new Set(initial.unitIds));
  const [pickedSessions, setPickedSessions] = useState(() => new Set(initial.sessionIds));
  useEscape(onClose);

  useEffect(() => {
    let active = true;
    void Promise.all([knowledgeApi.sources(baseId), knowledgeApi.knowledgeUnits(baseId), knowledgeApi.sessions(baseId)]).then(([sourceItems, unitItems, sessionItems]) => {
      if (!active) return;
      const trusted = unitItems.filter((unit) => unit.status === "trusted");
      setSources(sourceItems);
      setUnits(trusted);
      setSessions(sessionItems);
      // A choice made earlier may name something that has since gone.
      setPickedSources((current) => new Set([...current].filter((id) => sourceItems.some((item) => item.id === id))));
      setPickedUnits((current) => new Set([...current].filter((id) => trusted.some((item) => item.id === id))));
      setPickedSessions((current) => new Set([...current].filter((id) => sessionItems.some((item) => item.id === id))));
    }).catch((reason) => {
      if (active) setProblem(reason instanceof Error ? reason.message : "The library could not be read.");
    });
    return () => { active = false; };
  }, [baseId]);

  const matches = (title: string) => !filter.trim() || title.toLowerCase().includes(filter.trim().toLowerCase());
  const sourceRows: Row[] = (sources ?? []).filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: item.kind }));
  const unitRows: Row[] = units.filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: "" }));
  const sessionRows: Row[] = sessions.filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: plural(item.messageCount, "message") }));

  const toggle = (set: Set<string>, setter: (next: Set<string>) => void) => (id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };
  const choose = (set: Set<string>, setter: (next: Set<string>) => void, rows: Row[]) => (select: boolean) => {
    const next = new Set(set);
    for (const row of rows) {
      if (select) next.add(row.id);
      else next.delete(row.id);
    }
    setter(next);
  };

  const counts: ScopeCounts = { sources: pickedSources.size, units: pickedUnits.size, sessions: pickedSessions.size };
  const chosen = counts.sources + counts.units + counts.sessions;
  const apply = () => onApply({
    mode: "selection",
    sourceIds: [...pickedSources],
    unitIds: [...pickedUnits],
    sessionIds: [...pickedSessions],
  });

  return createPortal(
    <section className="atlas-overlay" role="dialog" aria-modal="true" aria-labelledby="outputs-picker-title" onMouseDown={onClose}>
      <div className="outputs-picker" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <h2 id="outputs-picker-title">Choose what to use</h2>
          <p>An output is built only from what you choose here.</p>
        </header>
        <label className="outputs-picker-filter">
          <Search size={14} aria-hidden="true" />
          <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by title" aria-label="Filter by title" />
        </label>
        {problem && <div className="gx-banner is-error" role="alert"><CircleAlert size={16} /><span><strong>Couldn’t read this library</strong><small>{problem}</small></span></div>}
        {!problem && !sources && <div className="outputs-picker-wait"><LoaderCircle className="spin" size={15} />Reading the library…</div>}
        {sources && <div className="outputs-picker-groups">
          <Group title="Sources" rows={sourceRows} picked={pickedSources} onToggle={toggle(pickedSources, setPickedSources)} onAll={choose(pickedSources, setPickedSources, sourceRows)} empty="No sources" />
          <Group title="Saved knowledge" rows={unitRows} picked={pickedUnits} onToggle={toggle(pickedUnits, setPickedUnits)} onAll={choose(pickedUnits, setPickedUnits, unitRows)} empty="Nothing saved yet" />
          <Group title="Discussions" rows={sessionRows} picked={pickedSessions} onToggle={toggle(pickedSessions, setPickedSessions)} onAll={choose(pickedSessions, setPickedSessions, sessionRows)} empty="No discussions" />
        </div>}
        <footer>
          <span>{describeScope(counts)}</span>
          <div>
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onClose}>Cancel</button>
            <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={chosen === 0} onClick={apply}>Use these</button>
          </div>
        </footer>
      </div>
    </section>,
    document.body,
  );
}
