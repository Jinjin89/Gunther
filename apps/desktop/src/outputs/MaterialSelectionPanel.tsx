import type { OutputScope } from "@gunther/contracts";
import { CircleAlert, LoaderCircle, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { describeScope, ScopeGroup, type ScopeCounts } from "./ScopePicker";
import { SourceScopeTree } from "./SourceScopeTree";
import { loadScopeMaterials, type ScopeMaterials } from "./scopeMaterials";

/** A persistent material list: selecting sources changes the next artifact's scope. */
export function MaterialSelectionPanel({ baseId, scope, onChange, onLoaded, disabled = false, onAdd }: {
  baseId: string;
  scope: OutputScope;
  onChange: (scope: OutputScope) => void;
  onLoaded: (counts: ScopeCounts) => void;
  disabled?: boolean;
  onAdd: () => void;
}) {
  const [materials, setMaterials] = useState<ScopeMaterials | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setMaterials(null); setProblem(null);
    void loadScopeMaterials(baseId).then((next) => {
      if (!active) return;
      setMaterials(next);
      onLoaded({ sources: next.sources.length, units: next.units.length, sessions: 0 });
    }).catch((reason) => { if (active) setProblem(reason instanceof Error ? reason.message : "The library could not be read."); });
    return () => { active = false; };
  }, [baseId, reload, onLoaded]);
  useEffect(() => {
    const refresh = () => setReload((value) => value + 1);
    window.addEventListener("gunther:sources-updated", refresh);
    window.addEventListener("gunther:topics-updated", refresh);
    window.addEventListener("gunther:knowledge-updated", refresh);
    return () => {
      window.removeEventListener("gunther:sources-updated", refresh);
      window.removeEventListener("gunther:topics-updated", refresh);
      window.removeEventListener("gunther:knowledge-updated", refresh);
    };
  }, []);
  useEffect(() => setFilter(""), [baseId]);
  const picked = useMemo(() => ({
    sources: new Set(scope.mode === "library" ? materials?.sources.map((item) => item.id) ?? [] : scope.sourceIds),
    units: new Set(scope.mode === "library" ? materials?.units.map((item) => item.id) ?? [] : scope.unitIds),
    sessions: new Set(scope.mode === "library" ? [] : scope.sessionIds),
  }), [scope, materials]);
  const matches = (title: string) => !filter.trim() || title.toLowerCase().includes(filter.trim().toLowerCase());
  const choose = (kind: keyof typeof picked, ids: string[], select: boolean) => {
    if (disabled) return;
    const next = new Set(picked[kind]);
    ids.forEach((id) => select ? next.add(id) : next.delete(id));
    onChange({ mode: "selection", sourceIds: [...(kind === "sources" ? next : picked.sources)], unitIds: [...(kind === "units" ? next : picked.units)], sessionIds: [...(kind === "sessions" ? next : picked.sessions)] });
  };
  const units = materials?.units.filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: "Saved knowledge" })) ?? [];
  const sessions = materials?.sessions.filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: `${item.messageCount} ${item.messageCount === 1 ? "message" : "messages"}` })) ?? [];
  const counts = { sources: picked.sources.size, units: picked.units.size, sessions: picked.sessions.size };
  return <aside className="artifacts-materials" aria-label="Source selection">
    <header><h2>Sources</h2><button type="button" className="gx-icon-button" onClick={onAdd} aria-label="Add source"><Plus size={14} /></button></header>
    <label className="artifacts-material-filter"><Search size={14} aria-hidden="true" /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter materials" aria-label="Filter materials" /></label>
    {problem && <div className="gx-banner is-error" role="alert"><CircleAlert size={14} /><span><strong>Couldn’t read the materials</strong><small>{problem}</small><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setReload((value) => value + 1)}>Try again</button></span></div>}
    {!problem && !materials && <p className="outputs-picker-wait" role="status"><LoaderCircle className="spin" size={14} />Reading the library…</p>}
    {materials && <fieldset className="artifacts-material-options" disabled={disabled}>
      {materials.contents ? <SourceScopeTree contents={materials.contents} filter={filter} picked={picked.sources} onChoose={(ids, select) => choose("sources", ids, select)} /> : <>
        <p className="outputs-source-hint" role="status">Contents couldn’t be loaded. Individual sources remain available.</p>
        <ScopeGroup title="Sources" rows={materials.sources.filter((item) => matches(item.title)).map((item) => ({ id: item.id, title: item.title, meta: item.kind }))} picked={picked.sources} onToggle={(id) => choose("sources", [id], !picked.sources.has(id))} onAll={(select) => choose("sources", materials.sources.filter((item) => matches(item.title)).map((item) => item.id), select)} empty="No sources" />
      </>}
      {(materials.units.length > 0 || materials.sessions.length > 0 || picked.units.size > 0 || picked.sessions.size > 0) && <details className="artifacts-other-materials" open={picked.sessions.size > 0 || undefined}>
        <summary>Saved knowledge & discussions</summary>
        {materials.units.length > 0 && <ScopeGroup title="Saved knowledge" rows={units} picked={picked.units} onToggle={(id) => choose("units", [id], !picked.units.has(id))} onAll={(select) => choose("units", units.map((item) => item.id), select)} empty="No matching knowledge" />}
        {materials.sessions.length > 0 && <ScopeGroup title="Discussions" rows={sessions} picked={picked.sessions} onToggle={(id) => choose("sessions", [id], !picked.sessions.has(id))} onAll={(select) => choose("sessions", sessions.map((item) => item.id), select)} empty="No matching discussions" />}
      </details>}
    </fieldset>}
    <footer><span>For the next artifact</span><strong aria-live="polite">{materials ? describeScope(counts) : "Reading the library…"}</strong><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={disabled || !materials} onClick={() => onChange({ mode: "library", sourceIds: [], unitIds: [], sessionIds: [] })}>Use whole library</button></footer>
  </aside>;
}
