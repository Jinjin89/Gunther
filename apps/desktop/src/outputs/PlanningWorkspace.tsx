import {
  artifactAudiences, type ApprovedCreationPlan, type ArtifactConversationDetail, type ArtifactMark, type ArtifactWork, type ModelMenu,
  type CreationTurn, type OutputScope, type OutputType, type PlanAnswer, type PlanCount, type PlanCountEdit,
  type PlanLine, type PlanOptions, type PlanPart, type PlanPreferences, type PlanSetup, type WorkPlan, type WorkPlanPatch,
} from "@gunther/contracts";
import { createContext, useContext, useId, useLayoutEffect, useRef, useState, type FocusEvent, type ReactNode } from "react";
import { ApiRequestError, OutputStoppedError } from "../api";
import { resolveEffort } from "../models/askModel";
import { artifactRequestId, scopeLabel } from "./artifactConversations";
import { ArtifactMessageList } from "./ArtifactMessages";
import { OutlineEditor, outlineIssue, partEdits } from "./OutlineEditor";
import { collectLineEntries, EditableLineList, finishLineEdit, hasLineEntries, LINE_LISTS, PreferenceFields,
  type ChangeLineList, type LineEntries } from "./PreferenceFields";
import { StudioWebSwitch } from "./StudioWebSwitch";
import type { LocalSetup, PlanAddress, SourceState, StudioPlanning } from "./useStudio";
import { VoiceFields } from "./VoiceFields";
import "./planning.css";

export interface PlanningWorkspaceProps {
  address: PlanAddress; type: OutputType; controller: StudioPlanning;
  detail: ArtifactConversationDetail | null; work: ArtifactWork | null; detailReadComplete: boolean;
  sourceState: (scope: OutputScope) => SourceState; currentScope: OutputScope; modelMenu: ModelMenu | null; webAvailable: boolean;
  busy: boolean; stage: string | null; onStop: () => void; dirty: { current: boolean };
  editFlush?: EditFlush;
}

export type EditFlush = { current: (() => Promise<boolean>) | null };
type ReadDetail = (conversationId: string) => Promise<ArtifactConversationDetail | null>;
// The reader owns scoped GETs; use the same read/store path to recover from a conflict.
export const PlanDetailReader = createContext<ReadDetail | null>(null);

type Preferences = Pick<PlanSetup, "emphasize" | "avoid">;
type Fields = Required<Preferences> & {
  options: PlanOptions | null;
  outline: PlanPart[] | null;
  coverage: PlanLine[] | null;
  count: { mode: PlanCount["mode"]; value: string } | null;
};
type Snapshot = { detail: ArtifactConversationDetail; work: ArtifactWork; fields: Fields };
type Difference = { label: string; mine: string; latest: string };
type Conflict = { differences: Difference[]; latest: Fields; base: Fields; mine: Fields };
type Revision = { editVersion: number; planRevision: string | undefined };
type Draft = {
  base: Snapshot; fields: Fields; entries: LineEntries; saving: boolean; conflict: Conflict | null;
  acknowledgment: { from: Revision; to: Revision } | null;
  problem: string; notice: string; inputEpoch: number; remoteEpoch: number;
};

const EMPTY_MARKS: Readonly<Record<string, ArtifactMark>> = {};
const OUTLINE_FIELDS = ["heading", "goal", "layout", "notes"] as const;
const LINE_FIELDS = ["text"] as const;
const MODEL_BLOCKER = "Choose an available model.";
const EFFORT_BLOCKER = "Choose a supported effort for this model.";
const PLAN_CHANGED = "The plan changed. Review the current plan before generating.";
const OPTION_LABELS: Partial<Record<keyof PlanOptions, string>> = {
  scope: "Source scope", focus: "Focus", title: "Title", audience: "Audience", length: "Length",
  language: "Language", use: "Use", theme: "Theme", voices: "Voices", web: "Search the web",
  model: "Model", effort: "Effort", designVersion: "Design version",
};

function errorMessage(reason: unknown) {
  if (reason instanceof ApiRequestError && Array.isArray(reason.detail)) {
    const issues = reason.detail.flatMap((issue: unknown) => {
      if (!issue || typeof issue !== "object" || !("msg" in issue) || typeof issue.msg !== "string") return [];
      const location = "loc" in issue && Array.isArray(issue.loc) ? issue.loc.filter((part) =>
        typeof part === "string" || typeof part === "number",
      ).map((part) => typeof part === "number" ? `item ${part + 1}` : String(part)) : [];
      return [location.length ? `${location.join(" · ")} — ${issue.msg}` : issue.msg];
    });
    if (issues.length) return issues.join(" ");
  }
  return reason instanceof Error ? reason.message : "The request could not be completed.";
}
const isConflict = (reason: unknown): reason is ApiRequestError => reason instanceof ApiRequestError && reason.status === 409;

function same(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((entry, index) => same(entry, b[index]));
  }
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  return Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([key, value]) =>
    Object.hasOwn(b, key) && same(value, (b as Record<string, unknown>)[key]));
}

function canonicalScope(scope: OutputScope): OutputScope {
  return scope.mode === "library" ? { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] } : {
    mode: "selection", sourceIds: [...scope.sourceIds].sort(), unitIds: [...scope.unitIds].sort(), sessionIds: [...scope.sessionIds].sort(),
  };
}
export const sameScope = (a: OutputScope, b: OutputScope) => same(canonicalScope(a), canonicalScope(b));

function revision(snapshot: Snapshot): Revision {
  return { editVersion: snapshot.detail.editVersion ?? 0, planRevision: snapshot.work.plan?.planRevision };
}

function tidyOptions<T extends PlanSetup>(options: T): T {
  return {
    ...options, scope: canonicalScope(options.scope),
    ...(options.focus !== undefined ? { focus: options.focus.trim() } : {}),
    ...(options.title !== undefined ? { title: options.title?.trim() || null } : {}),
    ...(options.language !== undefined ? { language: options.language?.trim() || null } : {}),
    ...(options.voices ? { voices: [options.voices[0].trim(), options.voices[1].trim()] } : {}),
  };
}

const cleanLines = (lines: PlanLine[]) => lines.map(({ id, text }) => ({ id, text: text.trim() }));
const rowOrder = (rows: { id: string }[]) => rows.map((row) => row.id);
function countEdit(count: NonNullable<Fields["count"]>): PlanCountEdit {
  return count.mode === "auto" ? { mode: "auto" } : { mode: count.mode, value: Number(count.value) };
}

function snapshot(detail: ArtifactConversationDetail, work: ArtifactWork): Snapshot {
  const plan = work.versionId === null ? work.plan : undefined;
  const preferences = plan?.preferences ?? detail.brief;
  const count = plan?.proposal?.count;
  return { detail, work, fields: {
    options: plan?.options ?? null,
    emphasize: cleanLines(preferences.emphasize ?? []),
    avoid: cleanLines(preferences.avoid ?? []),
    outline: plan?.outline ?? null,
    coverage: plan?.proposal?.coverage ?? null,
    count: count ? { mode: count.mode, value: count.mode === "auto" ? "" : String(count.value) } : null,
  } };
}

function patchFor(before: Snapshot, draft: Fields): WorkPlanPatch | null {
  const patch: WorkPlanPatch = { baseEditVersion: before.detail.editVersion ?? 0 };
  const base = before.fields;
  if (draft.options && base.options) {
    const next = tidyOptions(draft.options);
    const previous = tidyOptions(base.options);
    const changed = Object.entries(next).filter(([key, value]) => key !== "type" && !same(value, previous[key as keyof PlanOptions]));
    if (changed.length) patch.options = Object.fromEntries(changed);
  }
  for (const field of ["emphasize", "avoid", "coverage"] as const) {
    const rows = draft[field];
    if (rows && !same(cleanLines(rows), base[field])) patch[field] = cleanLines(rows);
  }
  if (draft.outline && !same(partEdits(draft.outline), base.outline && partEdits(base.outline))) patch.outline = partEdits(draft.outline);
  if (draft.count && !same(countEdit(draft.count), base.count && countEdit(base.count))) patch.count = countEdit(draft.count);
  return Object.keys(patch).length > 1 ? patch : null;
}

function describeValue(value: unknown): string {
  if (typeof value === "string") return value || "Empty";
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (Array.isArray(value)) return value.map(describeValue).join(" · ") || "Empty";
  if (value && typeof value === "object") {
    if ("sourceIds" in value) return scopeLabel(value as OutputScope);
    if ("heading" in value) return describeValue(value.heading);
    if ("text" in value) return describeValue(value.text);
    if ("mode" in value) return value.mode === "auto" ? "Automatic" : `${value.mode} ${"value" in value ? value.value : ""}`;
  }
  return "Removed or automatic";
}

type Merge = <T>(label: string, base: T, mine: T, latest: T, equal?: (a: T, b: T) => boolean) => T;
const sameText = (a: unknown, b: unknown) => same(typeof a === "string" ? a.trim() : a, typeof b === "string" ? b.trim() : b);
const sameOptionalText = (a: unknown, b: unknown) => (typeof a === "string" ? a.trim() || null : a) ===
  (typeof b === "string" ? b.trim() || null : b);

function compareOrder(order: string[], a: string, b: string) {
  if (!order.includes(a) || !order.includes(b)) return undefined;
  return order.indexOf(a) < order.indexOf(b);
}

function mergeOrder<T extends { id: string }>(label: string, base: string[], mine: string[], latest: string[],
  rows: Map<string, T>, merge: Merge, preferMine: boolean, differences: Difference[]) {
  const ids = [...rows.keys()];
  const preceding = new Map(ids.map((id) => [id, new Set<string>()]));
  for (const [index, a] of ids.entries()) {
    for (const b of ids.slice(index + 1)) {
      const localOrder = compareOrder(mine, a, b);
      const remoteOrder = compareOrder(latest, a, b);
      const before = compareOrder(base, a, b);
      if (localOrder === undefined && remoteOrder === undefined && before === undefined) continue;
      if (localOrder !== undefined && remoteOrder !== undefined && localOrder !== remoteOrder && before === undefined) {
        differences.push({ label, mine: describeValue(mine.map((id) => rows.get(id))), latest: describeValue(latest.map((id) => rows.get(id))) });
      }
      const aFirst = localOrder === undefined ? remoteOrder ?? before :
        remoteOrder === undefined ? localOrder : before === undefined ? (preferMine ? localOrder : remoteOrder) :
          merge(label, before, localOrder, remoteOrder);
      if (aFirst) preceding.get(b)?.add(a); else preceding.get(a)?.add(b);
    }
  }
  const ordered: string[] = [];
  const available = [...new Set([...latest, ...mine, ...ids])].filter((id) => rows.has(id));
  while (ordered.length < ids.length) {
    const next = available.find((id) => preceding.has(id) && preceding.get(id)?.size === 0);
    if (!next) {
      differences.push({ label, mine: describeValue(mine.filter((id) => rows.has(id)).map((id) => rows.get(id))),
        latest: describeValue(latest.filter((id) => rows.has(id)).map((id) => rows.get(id))) });
      return [...new Set(preferMine ? [...mine, ...latest] : [...latest, ...mine])].filter((id) => rows.has(id));
    }
    ordered.push(next);
    preceding.delete(next);
    for (const prerequisites of preceding.values()) prerequisites.delete(next);
  }
  return ordered;
}

function mergeRows<T extends { id: string }>(label: string, before: T[] | null, local: T[] | null, remote: T[] | null,
  keys: readonly (keyof T)[], merge: Merge, preferMine: boolean, differences: Difference[]): T[] | null {
  if (!before || !local || !remote) return merge(label, before, local, remote);
  const [base, mine, latest] = [before, local, remote].map((list) => new Map(list.map((row) => [row.id, row]))) as [
    Map<string, T>, Map<string, T>, Map<string, T>,
  ];
  const order = rowOrder(before), localOrder = rowOrder(local), remoteOrder = rowOrder(remote);
  const kept = new Map<string, T>();
  for (const id of new Set([...order, ...localOrder, ...remoteOrder])) {
    const old = base.get(id), myRow = mine.get(id), newRow = latest.get(id);
    const rowLabel = `${label} · ${describeValue(old ?? myRow ?? newRow)}`;
    let row: T | undefined;
    if (!myRow || !newRow) {
      const surviving = myRow ?? newRow;
      const survivingOrder = myRow ? localOrder : remoteOrder;
      const moved = order.some((other) => {
        const position = compareOrder(survivingOrder, id, other);
        return position !== undefined && position !== compareOrder(order, id, other);
      });
      if (old && surviving && moved && same(old, surviving)) {
        differences.push({ label: rowLabel, mine: myRow ? "Moved" : "Removed", latest: newRow ? "Moved" : "Removed" });
        row = preferMine ? myRow : newRow;
      } else {
        row = merge(rowLabel, old, myRow, newRow);
      }
    } else if (!old) {
      row = merge(rowLabel, old, myRow, newRow);
    } else {
      row = { ...newRow };
      for (const key of keys) row[key] = merge(`${rowLabel} · ${String(key)}`, old[key], myRow[key], newRow[key], sameText);
    }
    if (row) kept.set(id, row);
  }
  return mergeOrder(`${label} order`, order, localOrder, remoteOrder, kept, merge, preferMine, differences).map((id) => kept.get(id)!);
}

function mergeFields(base: Fields, mine: Fields, latest: Fields, preferMine: boolean) {
  const differences: Difference[] = [];
  const merge: Merge = (label, before, local, remote, equal = same) => {
    if (equal(local, remote)) return local;
    if (same(before, local)) return remote;
    if (equal(before, remote)) return local;
    if (equal(before, local)) return remote;
    differences.push({ label, mine: describeValue(local), latest: describeValue(remote) });
    return preferMine ? local : remote;
  };
  let options: Fields["options"];
  if (base.options && mine.options && latest.options) {
    // Resolve options independently, including the ordered speakers.
    const before = base.options, local = mine.options, remote = latest.options;
    options = { ...latest.options };
    for (const key of Object.keys(options) as (keyof PlanOptions)[]) {
      if (key === "voices" && before.voices && local.voices && remote.voices) {
        options.voices = [
          merge("Speaker A voice", before.voices[0], local.voices[0], remote.voices[0], sameText),
          merge("Speaker B voice", before.voices[1], local.voices[1], remote.voices[1], sameText),
        ];
      } else {
        const equal = key === "scope" ? (a: unknown, b: unknown) => sameScope(a as OutputScope, b as OutputScope) :
          key === "title" || key === "language" ? sameOptionalText : key === "focus" ? sameText : same;
        Object.assign(options, { [key]: merge(OPTION_LABELS[key] ?? key, before[key], local[key], remote[key], equal) });
      }
    }
  } else {
    options = merge("Settings", base.options, mine.options, latest.options);
  }
  const fields: Fields = {
    options,
    emphasize: mergeRows(LINE_LISTS.emphasize.label, base.emphasize, mine.emphasize, latest.emphasize, LINE_FIELDS, merge, preferMine, differences) ?? [],
    avoid: mergeRows(LINE_LISTS.avoid.label, base.avoid, mine.avoid, latest.avoid, LINE_FIELDS, merge, preferMine, differences) ?? [],
    outline: mergeRows("Outline", base.outline, mine.outline, latest.outline, OUTLINE_FIELDS, merge, preferMine, differences),
    coverage: mergeRows("Coverage topics", base.coverage, mine.coverage, latest.coverage, LINE_FIELDS, merge, preferMine, differences),
    count: merge("Count", base.count, mine.count, latest.count),
  };
  return { fields, differences };
}

function useWorkEdits(detail: ArtifactConversationDetail, work: ArtifactWork, controller: StudioPlanning,
  validate: (fields: Fields) => string, dirty: { current: boolean } | undefined) {
  const read = useContext(PlanDetailReader);
  const state = useRef<Draft | null>(null);
  if (!state.current) {
    const base = snapshot(detail, work);
    state.current = { base, fields: base.fields, entries: {}, saving: false, conflict: null, acknowledgment: null,
      problem: "", notice: "", inputEpoch: 0, remoteEpoch: 0 };
  }
  const draft = state.current;
  const [, render] = useState(0);
  const active = useRef(true);
  const pending = useRef<Promise<boolean> | null>(null);
  const queued = useRef(false);
  const deferred = useRef<Snapshot | null>(null);
  const redraw = () => {
    if (!active.current) return;
    if (dirty) dirty.current = Boolean(hasLineEntries(draft.entries) || patchFor(draft.base, draft.fields) || draft.conflict);
    render((value) => value + 1);
  };
  const receive = (next: Snapshot, forceReview = false) => {
    const previous = draft.base;
    if ((next.detail.editVersion ?? 0) < (previous.detail.editVersion ?? 0)) return;
    const advanced = (next.detail.editVersion ?? 0) > (previous.detail.editVersion ?? 0) ||
      next.work.plan?.planRevision !== previous.work.plan?.planRevision ||
      !same(next.detail.brief, previous.detail.brief) || !same(reviewContract(next), reviewContract(previous));
    if (advanced || forceReview) {
      // Keep the original three-way base while a conflict is awaiting a decision.
      const hadLocalEdits = Boolean(draft.conflict || patchFor(previous, draft.fields));
      const base = draft.conflict?.base ?? previous.fields;
      const mine = draft.conflict?.mine ?? draft.fields;
      const merged = mergeFields(base, mine, next.fields, true);
      draft.conflict = merged.differences.length ? {
        differences: merged.differences, latest: mergeFields(base, mine, next.fields, false).fields, base, mine,
      } : null;
      draft.fields = merged.fields;
      draft.remoteEpoch++;
      if (forceReview || hadLocalEdits) draft.notice = previous.work.versionId === null
        ? PLAN_CHANGED : "The preferences changed. Review the latest values.";
    }
    draft.base = next;
    redraw();
  };

  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; if (dirty) dirty.current = false; };
  }, [dirty]);
  useLayoutEffect(() => {
    const latest = snapshot(detail, work);
    if (pending.current) {
      if ((latest.detail.editVersion ?? 0) >= (deferred.current?.detail.editVersion ?? 0)) deferred.current = latest;
    } else receive(latest);
  // Each committed projection is applied once, even if there is active typing in the editor.
  }, [detail, work]);

  const flush = (): Promise<boolean> => {
    if (pending.current) { queued.current = true; return pending.current; }
    if (draft.problem || draft.conflict || validate(draft.fields)) return Promise.resolve(false);
    if (!patchFor(draft.base, draft.fields)) return Promise.resolve(true);

    const save = async () => {
      let rebased = false;
      do {
        queued.current = false;
        const base = draft.base, sent = draft.fields;
        const input = patchFor(base, sent);
        if (!input) return !rebased;
        if (draft.conflict || validate(sent)) return false;
        try {
          const returned = await controller.patch(base.detail.id, base.work.key, input);
          const row = returned.works.find((item) => item.key === base.work.key);
          if (returned.id !== base.detail.id || !row || (base.work.plan && row.plan?.planId !== base.work.plan.planId)) {
            throw new Error("The edited work is unavailable. Reopen it to continue.");
          }
          const next = snapshot(returned, row);
          const from = revision(base), to = revision(next);
          if (to.editVersion < from.editVersion) throw new Error("The current plan could not be confirmed.");
          // Replay edits over the committed snapshot. Equal normalized text keeps its raw input.
          draft.fields = mergeFields(sent, draft.fields, next.fields, true).fields;
          draft.base = next;
          draft.problem = "";
          const noOp = to.editVersion === from.editVersion && to.planRevision === from.planRevision;
          if (!noOp && to.editVersion !== from.editVersion + 1) {
            draft.acknowledgment = null;
            receive(next, true);
            return false;
          }
          draft.acknowledgment = { from, to };
        } catch (reason) {
          if (!rebased && read && isConflict(reason) && (!reason.code || reason.code === "edit_conflict")) {
            rebased = true;
            const latest = await read(base.detail.id).catch(() => null);
            const row = latest?.works.find((item) => item.key === base.work.key);
            if (latest?.id === base.detail.id && row && (latest.editVersion ?? 0) > input.baseEditVersion) {
              receive(snapshot(latest, row), true);
              if (draft.conflict) return false;
              queued.current = true;
              continue;
            }
          }
          draft.problem = errorMessage(reason);
          return false;
        }
      } while (queued.current && active.current);
      return !rebased;
    };

    draft.saving = true;
    draft.problem = "";
    const request = Promise.resolve().then(save).finally(() => {
      pending.current = null;
      draft.saving = false;
      const latest = deferred.current;
      deferred.current = null;
      if (latest) {
        // A store echo from our PATCH has the acknowledged version/revision and is safe to apply.
        receive(latest);
      } else {
        redraw();
      }
    });
    pending.current = request;
    redraw();
    return request;
  };

  const edit = (fields: Partial<Fields>, completed: boolean) => {
    draft.fields = { ...draft.fields, ...fields };
    draft.inputEpoch++;
    draft.problem = "";
    redraw();
    if (completed) void flush();
  };
  const editList: ChangeLineList = (field, rows, entry) => {
    draft.entries = { ...draft.entries, [field]: entry };
    edit({ [field]: rows }, !same(rowOrder(draft.fields[field] ?? []), rowOrder(rows)));
  };
  const commitEntries = () => {
    const entries = collectLineEntries(draft.fields, draft.entries);
    if (!entries) return false;
    if (draft.conflict || draft.problem) return false;
    if (hasLineEntries(draft.entries)) {
      draft.entries = {};
      edit(entries, false);
    }
    return true;
  };
  const flushBeforeAction = async () => commitEntries() && await flush() && active.current;
  const editOutline = (outline: PlanPart[]) => edit({ outline }, !same(
    draft.fields.outline?.map(({ id, layout }) => [id, layout]), outline.map(({ id, layout }) => [id, layout]),
  ));
  const editOptions = (options: Partial<PlanOptions>, completed = false) => {
    if (draft.fields.options) edit({ options: { ...draft.fields.options, ...options } }, completed);
  };
  const resolve = (keep: boolean) => {
    if (!draft.conflict) return;
    if (!keep) draft.fields = draft.conflict.latest;
    draft.conflict = null;
    draft.problem = "";
    redraw();
    void flush();
  };
  const reset = () => {
    draft.fields = draft.base.fields; draft.entries = {}; draft.problem = ""; draft.inputEpoch++; redraw();
  };
  const retry = () => { draft.problem = ""; redraw(); void flush(); };
  const announce = (notice: string) => { draft.notice = notice; redraw(); };
  const onBlur = (event: FocusEvent) => {
    if (!hasLineEntries(draft.entries) && (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) void flush();
  };
  return { draft, edit, editList, editOptions, editOutline, commitEntries, flush, flushBeforeAction, resolve, reset, retry, announce,
    onBlur, isActive: () => active.current };
}

function useEditFlush(handle: EditFlush | undefined, flush: () => Promise<boolean>) {
  useLayoutEffect(() => {
    if (!handle) return;
    handle.current = flush;
    return () => { if (handle.current === flush) handle.current = null; };
  }, [handle, flush]);
}

const DEFAULT_AUDIENCE = "scientist";
const LENGTHS = ["short", "standard", "long"] as const;
const DIVISORS = { short: 4, standard: 2, long: 1 };
const THEMES = ["paper", "ink", "editorial", "brand", "classic", "briefing", "keynote"] as const;
const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function preferenceMarks(preferences: PlanPreferences, fields: Required<Preferences>) {
  const marks: Record<string, ArtifactMark> = {};
  for (const field of ["emphasize", "avoid"] as const) {
    const stored = new Map((preferences[field] ?? []).map((line) => [line.id, line]));
    for (const row of fields[field]) {
      const previous = stored.get(row.id);
      if (previous?.text === row.text.trim()) marks[row.id] = previous.mark;
    }
  }
  return marks;
}

function modelProblem(setup: PlanSetup, menu: ModelMenu | null) {
  if (!menu?.models.length) return "Choose an available model in Settings.";
  const model = menu.models.find((item) => item.ref === (setup.model ?? menu.default?.model));
  if (!model) return MODEL_BLOCKER;
  if (setup.effort && !model.levels.some((level) => level.id === setup.effort)) return EFFORT_BLOCKER;
  return "";
}

function settingsProblem(type: OutputType, setup: PlanSetup, props: Pick<PlanningWorkspaceProps,
  "modelMenu" | "sourceState" | "webAvailable">, needsWeb = true) {
  const provider = modelProblem(setup, props.modelMenu);
  if (provider) return provider;
  const sources = props.sourceState(setup.scope);
  if (sources === "loading") return "Sources are loading.";
  if (sources === "error") return "The sources couldn’t load. Reopen the library to try again.";
  if (sources === "empty") return setup.scope.mode === "selection"
    ? "Select sources in the Library and choose Use current selection."
    : "Add a source to the Library to continue.";
  if (needsWeb && setup.web && !props.webAvailable) return "Turn off web search or connect a web search provider in Settings.";
  if (type.options.includes("voices")) {
    const voices = type.voices;
    if (!voices || (!voices.defaults && !voices.allowCustom && !voices.choices.length)) return "Set up a voice provider in Settings.";
    const pair = setup.voices ?? voices.defaults;
    if (!pair?.every((voice) => voice.trim() && (voices.allowCustom || voices.defaults?.includes(voice)
      || voices.choices.some((choice) => choice.value === voice)))) return "Choose available voices for both speakers.";
  }
  return "";
}

function lineProblem(field: string, rows: PlanLine[]) {
  if (rows.some((row) => !row.text.trim() || row.text.trim().length > 300)) return `Complete each item in ${field} using at most 300 characters.`;
  if (rows.length > 8) return `Keep at most 8 items in ${field}.`;
  return "";
}
const preferenceProblem = (fields: Preferences) =>
  lineProblem("What to emphasize", fields.emphasize ?? []) || lineProblem("What to avoid", fields.avoid ?? []);

function automaticCount(type: OutputType, length: PlanOptions["length"] | undefined, fallback: number) {
  return type.partLimit === undefined ? fallback : Math.max(type.minParts ?? 1, Math.floor(type.partLimit / DIVISORS[length ?? "standard"]));
}

function workProblem(type: OutputType, fields: Fields) {
  if (fields.outline) {
    const problem = outlineIssue(fields.outline, type.partLimit ?? fields.outline.length);
    if (problem) return problem;
  } else if (!type.capabilities.outline && fields.coverage) {
    if (!fields.coverage.length) return "Add at least one coverage topic.";
    const problem = lineProblem("Coverage topics", fields.coverage);
    if (problem) return problem;
    if (fields.count && fields.count.mode !== "auto") {
      const number = Number(fields.count.value);
      const min = type.minParts ?? 1, max = type.partLimit;
      if (!Number.isSafeInteger(number) || number < min || (max !== undefined && number > max)) {
        return max === undefined ? `Enter a whole number of at least ${min}.` : `Enter a whole number from ${min} to ${max}.`;
      }
    }
  }
  const voices = fields.options?.voices;
  if (voices?.some((voice) => !voice.trim() || voice.trim().length > 80)) return "Choose available voices for both speakers.";
  return preferenceProblem(fields);
}

function draftTitle(type: OutputType, base: Snapshot, fields: Fields) {
  const plan = base.work.plan;
  const firstRow = fields.outline?.[0];
  const priorFirst = base.fields.outline?.[0];
  const coverChanged = firstRow && (firstRow.id !== priorFirst?.id || firstRow.heading !== priorFirst.heading);
  return fields.options?.title?.trim() ||
    (type.outlineLayouts?.includes("title") && coverChanged ? firstRow.heading.trim() : "") ||
    base.work.title || plan?.proposal?.title || type.label;
}

function effectiveCount(type: OutputType, plan: NonNullable<WorkPlan["proposal"]>, fields: Fields): PlanCount {
  if (type.capabilities.outline) return { mode: "exact", value: fields.outline?.length ?? plan.count.value };
  if (fields.count?.mode === "auto") return { mode: "auto", value: automaticCount(type, fields.options?.length, plan.count.value) };
  return fields.count ? { mode: fields.count.mode, value: Number(fields.count.value) } : plan.count;
}

function completeOutline(rows: PlanPart[] | null) {
  return rows?.map((row) => ({
    message: "", question: "", link: "", requires: [], refs: [], requiredRefs: [],
    design: null, layoutLocked: false, designLocked: false, designAttempted: false, editedFields: [],
    ...row, layout: row.layout ?? null, notes: row.notes ?? "",
  })) ?? null;
}

function reviewContract({ detail, work }: Snapshot) {
  const plan = work.plan;
  if (!plan?.proposal) return null;
  return {
    brief: detail.brief,
    options: { ...plan.options, scope: canonicalScope(plan.options.scope) },
    preferences: plan.preferences,
    proposal: plan.proposal,
    outline: completeOutline(plan.outline),
  };
}

function reviewedWithEdits(type: OutputType, base: Snapshot, fields: Fields) {
  const contract = reviewContract(base);
  const patch = patchFor(base, fields);
  if (!contract || !patch) return contract;

  const { brief, preferences, proposal, outline } = contract;
  const updatedPreferences: Partial<PlanPreferences> = {};
  for (const field of ["emphasize", "avoid"] as const) {
    if (patch[field]) {
      updatedPreferences[field] = patch[field].map((row) =>
        preferences[field].find((previous) => previous.id === row.id && previous.text === row.text)
          ?? { ...row, mark: "edited" });
    }
  }
  const oldRows = new Map(outline?.map((row) => [row.id, row]));
  const nextOutline = patch.outline?.map((row) => {
    const previous = oldRows.get(row.id);
    const changed = OUTLINE_FIELDS.filter((field) => row[field] !== previous?.[field]);
    const claimChanged = changed.includes("heading") || changed.includes("goal");
    return { ...previous, ...row, ...(claimChanged ? { refs: [], requiredRefs: [] } : {}),
      editedFields: [...new Set([...(previous?.editedFields ?? []), ...changed])],
      layoutLocked: changed.includes("layout") ? row.layout !== null : previous?.layoutLocked ?? false };
  });
  return {
    brief: { ...brief, ...updatedPreferences },
    options: { ...contract.options, ...patch.options },
    preferences: { ...preferences, ...updatedPreferences },
    proposal: {
      ...proposal, title: patch.outline || patch.options?.title !== undefined ? draftTitle(type, base, fields) : proposal.title,
      coverage: patch.coverage ?? proposal.coverage,
      count: patch.outline || patch.count || (proposal.count.mode === "auto" && patch.options?.length !== undefined)
        ? effectiveCount(type, proposal, fields) : proposal.count,
    },
    outline: nextOutline ? completeOutline(nextOutline) : outline,
  };
}

const approvable = (work: ArtifactWork) => {
  const plan = work.plan;
  return work.versionId === null && Boolean(plan?.proposal && !plan.pending && plan.blocker === null
    && plan.attempt?.state !== "running" && (plan.planStatus === "ready" || plan.planStatus === "failed"));
};
const materialEdited = (before: PlanOptions, after: PlanOptions) => !sameScope(before.scope, after.scope) || (!before.web && after.web);

function PlanSettings({ type, value, preferences, entries, marks, optionsChanged, preferencesChanged, currentScope,
  currentSourceState, modelMenu, webAvailable, disabled, title, onBlur }: {
  type: OutputType; value: PlanSetup; preferences: Required<Preferences>; marks: Readonly<Record<string, ArtifactMark>>;
  entries: LineEntries;
  optionsChanged: (options: Partial<PlanOptions>, completed?: boolean) => void;
  preferencesChanged: ChangeLineList;
  currentScope: OutputScope; currentSourceState: SourceState; modelMenu: ModelMenu | null;
  webAvailable: boolean; disabled: boolean; title?: string; onBlur?: (event: FocusEvent) => void;
}) {
  const options = type.options;
  const defaultModel = modelMenu?.models.find((item) => item.ref === modelMenu.default?.model);
  const model = value.model ? modelMenu?.models.find((item) => item.ref === value.model) : defaultModel;
  const modelError = modelProblem(value, modelMenu);
  const chooseModel = (ref: string) => {
    const selected = modelMenu?.models.find((item) => item.ref === ref);
    if (!selected) return;
    const effort = modelMenu?.default?.model === ref ? modelMenu.default.effort : "high";
    optionsChanged({ model: ref, effort: resolveEffort(effort, selected.levels)?.id ?? null }, true);
  };
  return <section className="plan-settings plan-panel" aria-label="Plan settings" onBlur={onBlur}>
    <div className="plan-source-scope">
      <h2>Sources</h2>
      <p>Using {scopeLabel(value.scope)}</p>
      {!sameScope(value.scope, currentScope) && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
        disabled={disabled || currentSourceState !== "ready"} onClick={() => optionsChanged({ scope: currentScope }, true)}>
        Use current selection
      </button>}
    </div>
    <div className="plan-fields" aria-label="Output options">
      {title !== undefined && <label>Title
        <input value={value.title ?? ""} placeholder={title} maxLength={160} disabled={disabled} onKeyDown={finishLineEdit}
          onChange={(event) => optionsChanged({ title: event.currentTarget.value })} />
      </label>}
      {options.includes("focus") && <label>Focus
        <textarea value={value.focus ?? ""} disabled={disabled} rows={3} maxLength={2_000}
          onChange={(event) => optionsChanged({ focus: event.currentTarget.value })} />
      </label>}
      {options.includes("audience") && <label>Audience
        <select value={value.audience ?? DEFAULT_AUDIENCE} disabled={disabled}
          onChange={(event) => optionsChanged({ audience: event.currentTarget.value as PlanOptions["audience"] }, true)}>
          {artifactAudiences.map((audience) => <option key={audience} value={audience}>{capitalize(audience)}</option>)}
        </select>
      </label>}
      {options.includes("length") && <label>Length
        <select value={value.length ?? ""} disabled={disabled}
          onChange={(event) => optionsChanged({ length: (event.currentTarget.value || null) as PlanOptions["length"] }, true)}>
          <option value="">Default (standard)</option>
          {LENGTHS.map((length) => <option key={length} value={length}>{capitalize(length)}</option>)}
        </select>
      </label>}
      {options.includes("language") && <label>Language
        <input value={value.language ?? ""} disabled={disabled} maxLength={100} placeholder="Automatic" onKeyDown={finishLineEdit}
          onChange={(event) => optionsChanged({ language: event.currentTarget.value })} />
      </label>}
      {options.includes("use") && <label>Use
        <select value={value.use ?? ""} disabled={disabled}
          onChange={(event) => optionsChanged({ use: (event.currentTarget.value || null) as PlanOptions["use"] }, true)}>
          <option value="">Default (present)</option><option value="talk">Present</option><option value="read">Read</option>
        </select>
      </label>}
      {type.capabilities.design && <label>Theme
        <select value={value.theme ?? ""} disabled={disabled}
          onChange={(event) => optionsChanged({ theme: (event.currentTarget.value || null) as PlanOptions["theme"] }, true)}>
          <option value="">Default (paper)</option>
          {THEMES.map((theme) => <option key={theme} value={theme}>{capitalize(theme)}</option>)}
        </select>
      </label>}
      {options.includes("voices") && (type.voices ? <VoiceFields options={type.voices} value={value.voices ?? null}
        disabled={disabled} onChange={(voices) => optionsChanged({ voices }, !type.voices?.allowCustom)} /> :
        <small className="plan-hint">Set up a voice provider to choose voices.</small>)}
      <StudioWebSwitch available={webAvailable} checked={value.web ?? false} disabled={disabled}
        onChange={(web) => optionsChanged({ web }, true)} showHint allowTurnOff />
    </div>
    {modelMenu && modelMenu.models.length > 0 && <div className="plan-fields" aria-label="Model settings">
      <label>Model
        <select value={value.model ?? ""} disabled={disabled} aria-invalid={modelError === MODEL_BLOCKER || undefined}
          onChange={(event) => chooseModel(event.currentTarget.value || defaultModel?.ref || "")}>
          <option value="" disabled={!defaultModel}>{defaultModel ? `Current default (${defaultModel.label})` : "Choose a model"}</option>
          {value.model && !model && <option value={value.model} disabled>{value.model} (unavailable)</option>}
          {modelMenu.models.map((item) => <option key={item.ref} value={item.ref}>{item.display || item.label}</option>)}
        </select>
      </label>
      {Boolean(model?.levels.length) && <label>Effort
        <select value={value.effort ?? ""} disabled={disabled} aria-invalid={modelError === EFFORT_BLOCKER || undefined}
          onChange={(event) => optionsChanged({ effort: (event.currentTarget.value || null) as PlanOptions["effort"] }, true)}>
          <option value="">Default</option>
          {modelError === EFFORT_BLOCKER && <option value={value.effort ?? ""} disabled>{value.effort} (unavailable)</option>}
          {model?.levels.map((level) => <option key={level.id} value={level.id}>{level.label}</option>)}
        </select>
      </label>}
      {defaultModel && (modelError || value.model === null) && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
        disabled={disabled} onClick={() => chooseModel(defaultModel.ref)}>Use current model</button>}
    </div>}
    <PreferenceFields value={preferences} entries={entries} marks={marks} disabled={disabled} onChange={preferencesChanged} />
  </section>;
}

function PlanCountFields({ count, type, automatic, disabled, onChange }: {
  count: NonNullable<Fields["count"]>; type: OutputType; automatic: number; disabled: boolean;
  onChange: (count: NonNullable<Fields["count"]>, completed: boolean) => void;
}) {
  const min = type.minParts ?? 1, max = type.partLimit;
  const number = Number(count.value);
  const invalid = !Number.isSafeInteger(number) || number < min || (max !== undefined && number > max);
  return <div className="plan-count plan-fields">
    <label>Count<select value={count.mode} disabled={disabled} onChange={(event) => {
      const mode = event.currentTarget.value as PlanCount["mode"];
      onChange({ mode, value: mode === "auto" ? "" : count.value || String(automatic) }, true);
    }}>
      <option value="auto">Automatic</option><option value="target">Target</option><option value="exact">Exact</option>
    </select></label>
    {count.mode === "auto" ? <p className="plan-hint">Aims for {automatic} {type.capabilities.unitLabel}{automatic === 1 ? "" : "s"}.</p> :
      <label>Number of {type.capabilities.unitLabel}s
        <input type="number" min={min} max={max} step={1} value={count.value} disabled={disabled} onKeyDown={finishLineEdit}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange({ ...count, value: event.currentTarget.value }, false)} />
      </label>}
  </div>;
}

function PlanQuestionCard({ question, disabled, onAnswer }: {
  question: NonNullable<WorkPlan["pending"]>; disabled: boolean; onAnswer: (answer: PlanAnswer) => Promise<boolean>;
}) {
  const [answer, setAnswer] = useState("");
  return <section className="plan-panel plan-question" aria-label="Planning question">
    <h2 tabIndex={-1} data-plan-focus>Question</h2>
    <p>{question.question}</p>
    {question.options.length > 0 && <div className="artifact-pills" role="group" aria-label="Answer choices">
      {question.options.map((option, index) => <button key={index} type="button" className="artifact-pill"
        disabled={disabled} onClick={() => void onAnswer({ type: "choice", index })}>{option}</button>)}
    </div>}
    <form className="plan-fields" aria-label="Answer question" onSubmit={(event) => {
      event.preventDefault();
      if (!disabled && answer.trim()) void onAnswer({ type: "text", text: answer.trim() }).then((sent) => { if (sent) setAnswer(""); });
    }}>
      <label>Your answer<input value={answer} maxLength={20_000} disabled={disabled} onChange={(event) => setAnswer(event.currentTarget.value)} /></label>
      <div className="plan-actions">
        <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={disabled || !answer.trim()}>Send answer</button>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={disabled}
          onClick={() => void onAnswer({ type: "use_settings" })}>Use current settings</button>
      </div>
    </form>
  </section>;
}

function CountBadge({ count, unit }: { count: PlanCount; unit: string }) {
  const number = Number.isFinite(count.value) && count.value > 0 ? count.value : "–";
  return <span className="plan-badge">{
    count.mode === "auto" ? "Automatic · " : count.mode === "target" ? "Target · " : "Exact · "
  }{number} {unit}{number === 1 ? "" : "s"}</span>;
}

function EffectiveOptions({ options, type, reviewedWeb = false }: { options: PlanSetup; type: OutputType; reviewedWeb?: boolean }) {
  const use = options.use === "read" ? "Read" : "Present";
  const voice = (id: string) => type.voices?.choices.find((choice) => choice.value === id)?.label ?? id;
  return <ul className="plan-badges plan-effective-options" aria-label="Effective settings">
    <li>{capitalize(options.audience ?? DEFAULT_AUDIENCE)}</li>
    <li>{capitalize(options.length ?? "standard")}</li>
    <li>{options.language?.trim() || "Automatic language"}</li>
    {type.options.includes("use") && <li>{use}</li>}
    {type.capabilities.design && <li>{capitalize(options.theme ?? "paper")}</li>}
    {options.voices?.every((entry) => entry.trim()) && <li>A · {voice(options.voices[0])} / B · {voice(options.voices[1])}</li>}
    {options.web && <li>{reviewedWeb ? "Web search complete" : "Web search on"}</li>}
  </ul>;
}

function Progress({ stage, generating, onStop }: { stage: string | null; generating: boolean; onStop: () => void }) {
  return <div className="plan-progress plan-panel">
    <div className="plan-actions">
      <span role="status">{stage ?? (generating ? "Generating…" : "Planning…")}</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onStop}>Stop</button>
    </div>
    <progress aria-label={generating ? "Generation progress" : "Planning progress"} />
  </div>;
}

function SaveStatus({ draft }: { draft: Draft }) {
  return <p className="plan-save-status plan-hint" role="status">{draft.saving ? "Saving changes…" : draft.notice}</p>;
}

function EditStatus({ edits, showStatus = true }: { edits: ReturnType<typeof useWorkEdits>; showStatus?: boolean }) {
  const { conflict, problem } = edits.draft;
  return <>
    {showStatus && <SaveStatus draft={edits.draft} />}
    {problem && <div className="plan-problem" role="alert">
      <p>{problem}</p>
      <div className="plan-actions">
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={edits.retry}>Retry saving</button>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={edits.reset}>Discard unsaved edits</button>
      </div>
    </div>}
    {conflict && <section className="plan-problem" aria-label="Resolve editing conflict">
      <h3 role="alert">These fields changed in another view</h3>
      <ul>{conflict.differences.map((difference, index) => <li key={index}>
        <b>{difference.label}</b>
        <dl><dt>Your edit</dt><dd>{difference.mine}</dd><dt>Latest saved value</dt><dd>{difference.latest}</dd></dl>
      </li>)}</ul>
      <div className="plan-actions">
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => edits.resolve(true)}>Keep my edits</button>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => edits.resolve(false)}>Use latest values</button>
      </div>
    </section>}
  </>;
}

function PlanHistory({ detail, type }: { detail: ArtifactConversationDetail; type: OutputType }) {
  if (!detail.messages.length) return null;
  return <details className="output-history plan-history">
    <summary>Conversation history</summary>
    <ArtifactMessageList messages={detail.messages} kind={type.shape} unitLabel={() => type.capabilities.unitLabel} />
  </details>;
}

function PlanDisclosure({ type, options, proposal, outline, preferences, currentTitle }: {
  type: OutputType;
  options: PlanOptions;
  proposal: NonNullable<WorkPlan["proposal"]>;
  outline: PlanPart[] | null;
  preferences: PlanPreferences;
  currentTitle?: string | undefined;
}) {
  return <details className="output-history plan-disclosure">
    <summary>Approved plan</summary>
    <div className="plan-disclosure-body">
      {proposal.title.trim() !== (currentTitle ?? "").trim() && <p><strong>Approved title:</strong> {proposal.title}</p>}
      <p>{proposal.summary}</p>
      {proposal.angle && <p>{proposal.angle}</p>}
      <p>Using {scopeLabel(options.scope)}</p>
      <CountBadge count={proposal.count} unit={type.capabilities.unitLabel} />
      <EffectiveOptions options={options} type={type} reviewedWeb />
      {outline?.length
        ? <OutlineEditor value={outline} onChange={() => {}} unitLabel={type.capabilities.unitLabel}
          max={type.partLimit ?? outline.length} layouts={type.outlineLayouts ?? []} firstPartRole={type.firstPartRole} disabled readOnly />
        : proposal.coverage.length > 0 && <section aria-label="Coverage topics"><h3>Coverage topics</h3>
          <ol>{proposal.coverage.map((row) => <li key={row.id}>{row.text}</li>)}</ol></section>}
      {(["emphasize", "avoid"] as const).map((field) => preferences[field].length > 0 && <section key={field}>
        <h3>{LINE_LISTS[field].label}</h3>
        <ul>{preferences[field].map((row) => <li key={row.id}>{row.text}</li>)}</ul>
      </section>)}
    </div>
  </details>;
}

export function ApprovedPlanDisclosure({ value, type, currentTitle }: {
  value: ApprovedCreationPlan; type: OutputType; currentTitle?: string | undefined;
}) {
  return <PlanDisclosure type={type} options={value.options} proposal={value.proposal} outline={value.outline}
    preferences={value.preferences} currentTitle={currentTitle} />;
}

export function SavedPlanPreferences({ detail, work, controller, disabled, dirty, editFlush }: {
  detail: ArtifactConversationDetail; work: ArtifactWork; controller: StudioPlanning; disabled: boolean;
  dirty?: { current: boolean }; editFlush?: EditFlush;
}) {
  const edits = useWorkEdits(detail, work, controller, preferenceProblem, dirty);
  const disclosure = useRef<HTMLDetailsElement>(null);
  useEditFlush(editFlush, async () => {
    const saved = await edits.flushBeforeAction();
    if (!saved && disclosure.current) disclosure.current.open = true;
    return saved;
  });
  const { base, entries, fields, conflict } = edits.draft;
  return <details className="output-history saved-plan-preferences" ref={disclosure}>
    <summary>Preferences for future changes</summary>
    <div className="plan-disclosure-body" onBlur={edits.onBlur}>
      {base.detail.works.length > 1 && <p className="plan-hint">These preferences apply to future changes in this conversation.</p>}
      <PreferenceFields value={fields} entries={entries} marks={preferenceMarks(base.detail.brief, fields)} disabled={disabled || Boolean(conflict)}
        onChange={edits.editList} />
      {preferenceProblem(fields) && <p className="plan-field-error">{preferenceProblem(fields)}</p>}
      <EditStatus edits={edits} />
    </div>
  </details>;
}

type TurnRunner = (turn: CreationTurn) => Promise<boolean>;

function Setup({ local, props, running, turn, problem }: {
  local: LocalSetup; props: PlanningWorkspaceProps; running: boolean; turn: TurnRunner; problem: string;
}) {
  const { type, address, controller, currentScope, sourceState, modelMenu, webAvailable, onStop, stage, dirty, editFlush } = props;
  const { setup } = local;
  const [entries, setEntries] = useState<LineEntries>({});
  const blocker = settingsProblem(type, setup, props) || preferenceProblem(setup);
  const update = (changes: Partial<PlanSetup>) =>
    controller.setLocal({ ...local, setup: { ...setup, ...changes }, updatedAt: new Date().toISOString(), touched: true });
  useLayoutEffect(() => () => { dirty.current = false; }, [dirty]);
  const editList: ChangeLineList = (field, rows, entry) => {
    const next = { ...entries, [field]: entry };
    setEntries(next);
    dirty.current = hasLineEntries(next);
    if (field !== "coverage" && rows !== setup[field] && (rows.length || setup[field])) update({ [field]: rows });
  };
  const commitEntries = () => {
    const changes = collectLineEntries(setup, entries);
    if (!changes) return null;
    if (hasLineEntries(entries)) {
      update(changes);
      setEntries({});
      dirty.current = false;
    }
    return { ...setup, ...changes };
  };
  useEditFlush(editFlush, async () => Boolean(commitEntries()));
  const plan = () => {
    if (running || blocker) return;
    const reviewed = commitEntries();
    if (!reviewed) return;
    if (!local.touched && !hasLineEntries(entries)) update({});
    // Leave initialOutline unset so a resumed work keeps its full stored rows.
    void turn({ kind: "plan", clientRequestId: local.firstRequestId, planId: address.planId, setup: {
      ...tidyOptions(reviewed), emphasize: cleanLines(reviewed.emphasize ?? []), avoid: cleanLines(reviewed.avoid ?? []),
    } });
  };
  return <>
    <div className="reader-track" data-track="plan" data-stage="setup">
      <header className="plan-heading"><h1>Set up {type.label}</h1><p>{type.description}</p></header>
      <PlanSettings type={type} value={setup} preferences={{ emphasize: setup.emphasize ?? [], avoid: setup.avoid ?? [] }} entries={entries}
        marks={EMPTY_MARKS} optionsChanged={update} preferencesChanged={editList}
        currentScope={currentScope} currentSourceState={sourceState(currentScope)} modelMenu={modelMenu} webAvailable={webAvailable} disabled={running} />
    </div>
    <footer className="plan-footer">
      {running ? <Progress stage={stage} generating={false} onStop={onStop} /> :
        <div className="plan-actions"><button type="button" className="gx-btn gx-btn-primary" disabled={Boolean(blocker)} onClick={plan}>Plan</button>
          {blocker && <span className="plan-hint">{blocker}</span>}
        </div>}
      {problem && <p className="plan-problem" role="alert">{problem}</p>}
    </footer>
  </>;
}

function PersistedPlan({ props, detail, work, sending, turn, problem }: {
  props: PlanningWorkspaceProps; detail: ArtifactConversationDetail; work: ArtifactWork;
  sending: CreationTurn["kind"] | null; turn: TurnRunner; problem: string;
}) {
  const { type, address, controller, currentScope, sourceState, modelMenu, webAvailable, stage, onStop, dirty, editFlush } = props;
  const edits = useWorkEdits(detail, work, controller, (fields) => workProblem(type, fields), dirty);
  useEditFlush(editFlush, edits.flushBeforeAction);
  const { draft } = edits;
  const { base, fields, entries, conflict } = draft;
  const plan = base.work.plan!;
  const options = fields.options ?? plan.options;
  const question = plan.pending;
  const proposal = plan.proposal;
  const reviewing = Boolean(proposal || question);
  const activity = plan.attempt;
  const generating = plan.planStatus === "generating" || sending === "approve";
  const running = props.busy || Boolean(sending) || activity?.state === "running";
  const disabled = running || Boolean(conflict);
  const availability = settingsProblem(type, options, props);
  const approvalAvailability = settingsProblem(type, options, props, false);
  const invalid = workProblem(type, fields);
  const changedMaterial = materialEdited(plan.options, options);
  const planAgain = Boolean(proposal && (plan.blocker !== null || changedMaterial));
  const canApprove = approvable(base.work) && !changedMaterial && !approvalAvailability && !invalid && !disabled && !draft.problem;
  const [feedback, setFeedback] = useState("");
  const feedbackId = useId();
  const [acting, setActing] = useState(false);
  const action = useRef(false);
  const latestProps = useRef(props);
  const heading = useRef<HTMLHeadingElement>(null);
  const review = useRef<HTMLDivElement>(null);
  const focusAfterTurn = useRef(false);
  latestProps.current = props;

  useLayoutEffect(() => {
    if (!running && focusAfterTurn.current) {
      focusAfterTurn.current = false;
      (review.current?.querySelector<HTMLElement>("[data-plan-focus]") ?? heading.current)?.focus();
    }
  }, [running, plan.planRevision, question?.messageId]);

  const act = async (kind: "plan" | "answer" | "approve", content?: { answer: PlanAnswer } | { text: string }) => {
    if (action.current || disabled || invalid || (kind === "approve" ? !canApprove : Boolean(availability)) || !edits.commitEntries()) return false;
    const captured = kind === "approve" ? reviewedWithEdits(type, draft.base, draft.fields) : null;
    const capturedRevision = revision(draft.base);
    const inputEpoch = draft.inputEpoch, remoteEpoch = draft.remoteEpoch;
    action.current = true;
    setActing(true);
    try {
      if (!await edits.flush() || !edits.isActive()) return false;
      const committed = draft.base;
      const current = committed.work.plan;
      if (!current || current.planId !== address.planId || committed.work.versionId !== null) return false;
      const confirmedRevision = revision(committed);
      const receipt = draft.acknowledgment;
      const reviewedRevision = same(confirmedRevision, capturedRevision)
        || (receipt && same(receipt.from, capturedRevision) && same(receipt.to, confirmedRevision));
      const changed = draft.inputEpoch !== inputEpoch || draft.remoteEpoch !== remoteEpoch || draft.conflict || !reviewedRevision;
      if (changed || latestProps.current.busy || settingsProblem(type, current.options, latestProps.current, kind !== "approve")) {
        edits.announce(kind === "approve" ? PLAN_CHANGED : "The plan changed. Review the current settings and try again.");
        return false;
      }

      const request = { clientRequestId: artifactRequestId(), planId: address.planId, planRevision: current.planRevision };
      if (kind === "approve") {
        if (!approvable(committed.work) || !captured || !same(captured, reviewContract(committed))) {
          edits.announce(PLAN_CHANGED);
          return false;
        }
        edits.announce("");
        return await turn({ ...request, kind: "approve" });
      }

      if (kind === "answer" && content && "answer" in content && question?.messageId === current.pending?.messageId && current.pending) {
        focusAfterTurn.current = true;
        edits.announce("");
        return await turn({ ...request, kind: "answer", questionMessageId: current.pending.messageId, answer: content.answer });
      }
      if (kind === "plan" && !current.pending) {
        focusAfterTurn.current = true;
        const text = content && "text" in content ? content.text.trim() : "";
        edits.announce("");
        const sent = await turn({ ...request, kind: "plan", ...(text ? { text } : {}) });
        if (sent && text) setFeedback("");
        return sent;
      }
      return false;
    } finally {
      action.current = false;
      setActing(false);
    }
  };

  const recovery = activity && activity.state !== "running" ? activity : null;
  const retryPlanning = recovery?.phase === "plan";
  const needsFeedback = Boolean(proposal && !planAgain && !retryPlanning);
  const planBlocker = availability || invalid;
  const canPlan = !disabled && !acting && !question && !planBlocker && !draft.problem
    && (!needsFeedback || Boolean(feedback.trim()));
  const submitPlan = () => { if (canPlan) void act("plan", { text: feedback }); };
  const showFeedback = Boolean(proposal && !question && !generating);
  const planLabel = planAgain ? "Plan again" : retryPlanning ? "Retry planning" : proposal ? "Update plan" : "Plan";
  const title = draftTitle(type, base, fields);
  const status = generating ? "Generating" : running ? "Planning" : question ? "Needs answer" :
    planAgain ? "Blocked" : approvable(base.work) ? (plan.basis === "user_edited" || Boolean(patchFor(base, fields)) ?
      "Edited · Ready to generate" : "Ready to approve") : plan.planStatus === "failed" ? "Failed" : "Planning";
  const recoveryText = recovery ? `${recovery.phase === "plan" ? "Planning" : "Generation"} ${recovery.state}.` : "";
  const approveLabel = recovery?.phase === "generate" ? (recovery.state === "failed" ? "Review & retry" : "Review & resume") : "Approve & Generate";
  const count = proposal ? effectiveCount(type, proposal, fields) : null;
  const whyApproval = approvalAvailability || invalid || (changedMaterial ? "Plan with these sources before generating." : plan.blocker) ||
    (!proposal ? "Plan this output before generating." : "");

  return <><div className="reader-track" data-track="plan" data-stage={reviewing ? "review" : "setup"}>
    <div className="plan-review" ref={review} onBlur={edits.onBlur}>
      <header className="plan-heading">
        <h1 ref={heading} tabIndex={-1}>{proposal ? title : `${reviewing ? "Plan" : "Set up"} ${type.label}`}</h1>
        {!reviewing && <p>{type.description}</p>}
        <div className="plan-badges"><span className="plan-badge" data-plan-status={plan.planStatus}>{status}</span>
          {count && <CountBadge count={count} unit={type.capabilities.unitLabel} />}
        </div>
        <div className="plan-announcement" aria-live="polite" aria-atomic="true">
          <span key={question?.messageId ?? plan.planRevision}>{
            question ? `New planning question. ${question.question}` : proposal ? `Plan revision updated. ${status}.` : status
          }</span>
        </div>
      </header>
      {running && <Progress stage={stage} generating={generating} onStop={onStop} />}
      {generating && proposal ? <PlanDisclosure type={type} options={plan.options} proposal={proposal}
        outline={plan.outline} preferences={plan.preferences} currentTitle={title} /> : <>
        {proposal && <section className="plan-panel plan-summary" aria-label="Plan summary">
          <h2>{plan.basis === "user_edited" ? "Latest assistant summary" : "Proposal"}</h2>
          <p>{proposal.summary}</p>
          {proposal.angle && <p><strong>Angle</strong> {proposal.angle}</p>}
          {base.detail.brief.goal && <p><strong>Goal</strong> {base.detail.brief.goal.text}</p>}
          {base.detail.brief.audience && <p><strong>Audience</strong> {base.detail.brief.audience.text}</p>}
          {base.detail.brief.constraints.length > 0 && <div>
            <strong>Constraints</strong>
            <ul>{base.detail.brief.constraints.map((line, index) => <li key={index}>{line.text}</li>)}</ul>
          </div>}
          <EffectiveOptions options={options} type={type} reviewedWeb={!changedMaterial && approvable(base.work)} />
          {type.capabilities.outline && proposal.coverage.length > 0 && <details>
            <summary>Coverage topics</summary>
            <ol>{proposal.coverage.map((row) => <li key={row.id}>{row.text}</li>)}</ol>
          </details>}
        </section>}
        {question && <PlanQuestionCard key={question.messageId} question={question} disabled={disabled || acting || Boolean(availability) || Boolean(invalid)}
          onAnswer={(answer) => act("answer", { answer })} />}
        {type.capabilities.outline && fields.outline && <OutlineEditor value={fields.outline} onChange={edits.editOutline}
          unitLabel={type.capabilities.unitLabel} max={type.partLimit ?? fields.outline.length} layouts={type.outlineLayouts ?? []}
          firstPartRole={type.firstPartRole} readOnly={false} disabled={disabled} />}
        {!type.capabilities.outline && fields.coverage && fields.count && proposal && <section className="plan-panel plan-fields" aria-label="Coverage and size">
          <EditableLineList field="coverage" value={fields.coverage} entry={entries.coverage} disabled={disabled} onChange={edits.editList} />
          <PlanCountFields count={fields.count} type={type} disabled={disabled}
            automatic={automaticCount(type, options.length, proposal.count.value)}
            onChange={(next, completed) => edits.edit({ count: next }, completed)} />
        </section>}
      </>}
    </div>
    {!generating && <PlanSettings type={type} value={options} preferences={fields} entries={entries} marks={preferenceMarks(plan.preferences, fields)}
      optionsChanged={edits.editOptions} preferencesChanged={edits.editList} currentScope={currentScope} webAvailable={webAvailable}
      currentSourceState={sourceState(currentScope)} modelMenu={modelMenu}
      disabled={disabled} {...(proposal ? { title } : {})} onBlur={edits.onBlur} />}
    <div className="plan-followup">
      {recovery && !running && <div className="plan-problem" role={recovery.state === "failed" ? "alert" : "status"}>
        <p><strong>{recoveryText}</strong> {recovery.detail}</p>
      </div>}
      {problem && <p className="plan-problem" role="alert">{problem}</p>}
      {!generating && <EditStatus edits={edits} showStatus={running} />}
      {showFeedback && <form className="plan-fields plan-feedback" aria-label="Plan feedback" id={feedbackId} onSubmit={(event) => {
        event.preventDefault();
        submitPlan();
      }}>
        <label>What should change in this plan?
          <textarea rows={3} maxLength={20_000} disabled={disabled} value={feedback}
            onChange={(event) => setFeedback(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submitPlan();
              }
            }} />
        </label>
      </form>}
      <PlanHistory detail={base.detail} type={type} />
    </div>
  </div>
    {!running && <footer className="plan-footer">
      <div className="plan-actions">
        {proposal && <button type="button" className="gx-btn gx-btn-primary" disabled={!canApprove || acting}
          onClick={() => void act("approve")}>{approveLabel}</button>}
        {!question && <button type={showFeedback ? "submit" : "button"} form={showFeedback ? feedbackId : undefined}
          className={canApprove || needsFeedback ? "gx-btn gx-btn-quiet" : "gx-btn gx-btn-primary"}
          disabled={!canPlan} onClick={showFeedback ? undefined : submitPlan}>{planLabel}</button>}
        {!reviewing && planBlocker && <span className={availability ? "plan-hint" : "plan-field-error"}>{planBlocker}</span>}
      </div>
      <SaveStatus draft={draft} />
      {reviewing && whyApproval && <p className={invalid ? "plan-field-error" : "plan-hint"}>{whyApproval}</p>}
    </footer>}
  </>;
}

export function PlanningWorkspace(props: PlanningWorkspaceProps) {
  const { address, type, controller, detail, work, detailReadComplete, busy } = props;
  const read = useContext(PlanDetailReader);
  const [sending, setSending] = useState<CreationTurn["kind"] | null>(null);
  const sendingRef = useRef(false);
  const [problem, setProblem] = useState("");
  const [restoredRequestId] = useState(artifactRequestId);
  const local = controller.locals[address.planId] ?? (detail && work && !work.plan && work.versionId === null ? {
    ...address, firstRequestId: restoredRequestId, updatedAt: detail.updatedAt, touched: false,
    setup: {
      type: type.id, scope: detail.scope, focus: detail.brief.goal?.text ?? "",
      title: work.title || null,
      emphasize: cleanLines(detail.brief.emphasize ?? []), avoid: cleanLines(detail.brief.avoid ?? []),
      ...(work.options ?? restoredOptions(work, detail)),
    },
  } : undefined);
  const turn = async (input: CreationTurn) => {
    if (sendingRef.current || busy) return false;
    sendingRef.current = true;
    setSending(input.kind);
    setProblem("");
    try {
      await controller.turn(address.conversationId, input);
      return true;
    } catch (reason) {
      await read?.(address.conversationId).catch(() => null);
      if (!(reason instanceof OutputStoppedError)) setProblem(errorMessage(reason));
      return false;
    } finally {
      sendingRef.current = false;
      setSending(null);
    }
  };

  return <article className="output-reader" data-mode="plan" data-kind={type.shape} aria-label="Output planning">
    {detail && work?.plan ? <PersistedPlan props={props} detail={detail} work={work} sending={sending} turn={turn} problem={problem} /> :
      local ? <Setup local={local} props={props} running={busy || sending !== null} turn={turn} problem={problem} /> :
        <div className="reader-track plan-review" data-track="plan"><header className="plan-heading"><h1>Plan {type.label}</h1></header>
          {busy || sending ? <Progress stage={props.stage} generating={sending === "approve"} onStop={props.onStop} /> :
            <p role="status">{detailReadComplete ? "This plan is unavailable." : "Loading plan…"}</p>}
          {problem && <p className="plan-problem" role="alert">{problem}</p>}
        </div>}
  </article>;
}

function restoredOptions(work: ArtifactWork, detail: ArtifactConversationDetail): Partial<PlanSetup> {
  const audience = artifactAudiences.find((value) => value === detail.brief.audience?.text);
  const length = LENGTHS.find((value) => value === work.spec.length?.text);
  const use = work.spec.use?.text;
  return {
    ...(audience ? { audience } : {}),
    ...(length ? { length } : {}),
    ...(use === "talk" || use === "read" ? { use } : {}),
  };
}
