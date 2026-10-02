import type { AskSkill } from "@gunther/contracts";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../../api";
import { MatchedTitle, TriggerMenu, useTriggerMenu, type Trigger } from "./TriggerMenu";

const MAX_OPTIONS = 8;

let loaded: Promise<AskSkill[]> | null = null;

/** The skills the `/` menu offers, fetched once and kept for the rest of the session of the app. */
export function useAskSkills(): AskSkill[] {
  const [skills, setSkills] = useState<AskSkill[]>([]);
  useEffect(() => {
    let live = true;
    loaded ??= Promise.resolve()
      .then(() => knowledgeApi.askSkills())
      // A failure is not kept: the next composer tries again.
      .catch(() => { loaded = null; return [] as AskSkill[]; });
    void loaded.then((items) => { if (live && items.length) setSkills(items); });
    return () => { live = false; };
  }, []);
  return skills;
}

/** Forget what was fetched (for tests). */
export const forgetAskSkills = () => { loaded = null; };

/** Command prefix, then title word, then anywhere in either, then the description: best first. */
export const matchSkills = (skills: AskSkill[], query: string) => {
  const needle = query.trim().toLowerCase();
  const score = (skill: AskSkill) => {
    if (!needle) return 1;
    const title = skill.title.toLowerCase();
    if (skill.command.startsWith(needle)) return 4;
    if (title.split(/\s+/).some((word) => word.startsWith(needle))) return 3;
    if (skill.command.includes(needle) || title.includes(needle)) return 2;
    return skill.description.toLowerCase().includes(needle) ? 1 : 0;
  };
  return skills
    .map((skill, index) => ({ skill, index, score: score(skill) }))
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, MAX_OPTIONS)
    .map((candidate) => candidate.skill);
};

type Option = AskSkill & { id: string };

/**
 * The `/` menu: it opens only when `/` is the first character of the message, and lists the skills
 * that match what is typed after it. Pass its `onKeyDown` the key presses; `menu` is the listbox.
 */
export function useSkillMenu(text: string, caret: number, skills: AskSkill[], onPick: (skill: AskSkill, trigger: Trigger) => void, enabled = true) {
  const state = useTriggerMenu<Option>({
    text: enabled ? text : "",
    caret,
    char: "/",
    atStart: true,
    options: (query) => matchSkills(skills, query).map((skill) => ({ ...skill, id: skill.command })),
  });
  const query = state.trigger?.query ?? "";
  const menu = <TriggerMenu
    open={state.open}
    listId={state.listId}
    label="Skills"
    announce={`${state.options.length} ${state.options.length === 1 ? "skill" : "skills"} available`}
    empty={state.options.length === 0 ? <div className="gx-mention-empty">{skills.length ? `No skill matches “${query}”` : "No skills yet."}</div> : undefined}
  >
    {state.options.map((skill, index) => (
      <div
        key={skill.command}
        id={state.optionId(skill.command)}
        role="option"
        aria-label={`${skill.title}, /${skill.command}`}
        aria-selected={state.active?.command === skill.command}
        className={`gx-mention-option ${state.active?.command === skill.command ? "is-active" : ""}`}
        title={skill.description}
        onMouseDown={(event) => { event.preventDefault(); if (state.trigger) onPick(skill, state.trigger); }}
        onMouseEnter={() => state.setActiveIndex(index)}
      >
        <span className="gx-mention-title"><MatchedTitle title={skill.title} query={query} /></span>
        <small>/{skill.command}</small>
      </div>
    ))}
  </TriggerMenu>;
  return {
    ...state,
    menu,
    activeId: state.active ? state.optionId(state.active.command) : undefined,
    keyDown: (event: Parameters<typeof state.onKeyDown>[0]) => state.onKeyDown(event, (skill, trigger) => onPick(skill, trigger)),
  };
}

export type SkillBudget = "standard" | "deep";

/** The chosen skill, shown before the text of the message; × takes it off. A skill with budgets lets the reader pick one. */
export function SkillChip({ command, onRemove, budget, onBudgetChange }: { command: string; onRemove: () => void; budget?: SkillBudget; onBudgetChange?: (budget: SkillBudget) => void }) {
  const budgets = useAskSkills().find((skill) => skill.command === command)?.budgets ?? [];
  const choices = budgets.filter((item): item is SkillBudget => item === "standard" || item === "deep");
  return <span className="gx-mention gx-skill-chip">
    <span>/{command}</span>
    {onBudgetChange && choices.length > 1 && <span className="budget-toggle" role="group" aria-label="Research depth">
      {choices.map((item) => <button key={item} type="button" aria-pressed={(budget ?? choices[0]) === item} onClick={() => onBudgetChange(item)}>{item === "deep" ? "Deep" : "Standard"}</button>)}
    </span>}
    <button type="button" onClick={onRemove} aria-label={`Remove /${command}`}><X size={11} /></button>
  </span>;
}
