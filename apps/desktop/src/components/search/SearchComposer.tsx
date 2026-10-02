import { ArrowUp, AtSign, CornerDownLeft, Globe2, X } from "lucide-react";
import { forwardRef, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import type { KnowledgeBase } from "../../atlas";
import { LibraryGlyph } from "../../design/LibraryGlyph";
import { DictationStatus, MicGlyph } from "../Dictation";
import { useDictatedField } from "../../services/useDictatedField";
import { withShortcut } from "../../shortcuts/shortcuts";
import { SkillChip, useAskSkills, useSkillMenu, type SkillBudget } from "./skills";
import { MatchedTitle, TriggerMenu, useTriggerMenu, withoutTrigger } from "./TriggerMenu";

export interface SearchComposerHandle {
  focus: () => void;
}

interface SearchComposerProps {
  bases: KnowledgeBase[];
  value: string;
  mentionIds: string[];
  web: boolean;
  searching: boolean;
  placeholder?: string;
  onValueChange: (value: string) => void;
  onMentionsChange: (ids: string[]) => void;
  onWebChange: (web: boolean) => void;
  onSubmit: () => void;
  /** ⌘/Ctrl+↵: ask Gunther the question instead of searching for it. */
  onAsk?: (() => void) | undefined;
  onClear: () => void;
  /** Called on ↓ at the end of the text; return true when focus moved elsewhere. */
  onArrowDown?: (() => boolean) | undefined;
  /** The model and effort chips for questions asked from here. */
  picker?: ReactNode;
  /** The skill chosen with `/` (its command), which makes ↵ ask instead of search. */
  skill?: string | null;
  onSkillChange?: ((command: string | null) => void) | undefined;
  /** The depth chosen for a skill that has budgets (research). */
  budget?: SkillBudget | undefined;
  onBudgetChange?: ((budget: SkillBudget) => void) | undefined;
}

const MAX_OPTIONS = 8;
const MAX_TEXTAREA_HEIGHT = 168;

const acronym = (title: string) => title.split(/[\s-]+/).map((word) => word[0] ?? "").join("").toLowerCase();

/** Title prefix, then title word, then acronym, then field label — best matches first. */
const matchScore = (base: KnowledgeBase, needle: string) => {
  if (!needle) return 1;
  const title = base.title.toLowerCase();
  if (title.startsWith(needle)) return 4;
  if (title.split(/[\s-]+/).some((word) => word.startsWith(needle))) return 3;
  if (title.includes(needle) || acronym(base.title).startsWith(needle)) return 2;
  return base.eyebrow.toLowerCase().includes(needle) ? 1 : 0;
};

const matchLibraries = (bases: KnowledgeBase[], excluded: string[], query: string) => {
  const needle = query.trim().toLowerCase();
  return bases
    .filter((base) => !excluded.includes(base.id))
    .map((base, index) => ({ base, index, score: matchScore(base, needle) }))
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, MAX_OPTIONS)
    .map((candidate) => candidate.base);
};

const sourceCount = (base: KnowledgeBase) => {
  const count = base.indexedSourceCount ?? base.sourceCount;
  return `${count} ${count === 1 ? "source" : "sources"}`;
};

export const SearchComposer = forwardRef<SearchComposerHandle, SearchComposerProps>(function SearchComposer({
  bases,
  value,
  mentionIds,
  web,
  searching,
  placeholder,
  onValueChange,
  onMentionsChange,
  onWebChange,
  onSubmit,
  onAsk,
  onClear,
  onArrowDown,
  picker,
  skill = null,
  onSkillChange,
  budget,
  onBudgetChange,
}, ref) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  const pendingCaret = useRef<number | null>(null);
  const hintId = useId();
  const skills = useAskSkills();

  const dictation = useDictatedField(value, onValueChange, { onAppended: (next) => { pendingCaret.current = next.length; } });
  const { listening, dictating } = dictation;

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

  const mentioned = useMemo(() => mentionIds.map((id) => bases.find((base) => base.id === id)).filter((base): base is KnowledgeBase => Boolean(base)), [bases, mentionIds]);
  const libraries = useTriggerMenu({ text: value, caret, char: "@", options: (query) => matchLibraries(bases, mentionIds, query) });
  const { trigger, options, open: librariesOpen, active: activeOption } = libraries;
  const slash = useSkillMenu(value, caret, skills, (chosen, at) => selectSkill(chosen.command, at), Boolean(onSkillChange) && !skill);
  const menuOpen = librariesOpen || slash.open;
  const opensLibrary = !value.trim() && mentioned.length === 1;
  const canSubmit = Boolean(value.trim()) || opensLibrary;
  // A chosen skill is for a question: ↵ asks.
  const asks = Boolean(skill && onAsk && value.trim());

  useLayoutEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
    // Restore the caret once React has written an edited value into the field.
    if (pendingCaret.current !== null) {
      const position = Math.min(pendingCaret.current, element.value.length);
      pendingCaret.current = null;
      element.setSelectionRange(position, position);
      setCaret(position);
    }
  }, [value, mentionIds]);

  const placeCaret = (position: number) => {
    pendingCaret.current = position;
    textarea.current?.focus();
  };

  const selectLibrary = (base: KnowledgeBase) => {
    if (!trigger) return;
    onValueChange(withoutTrigger(value, trigger, textarea.current?.selectionStart ?? caret));
    onMentionsChange([...mentionIds, base.id]);
    libraries.reset();
    placeCaret(trigger.start);
  };

  const selectSkill = (command: string, at: { start: number; query: string }) => {
    onValueChange(withoutTrigger(value, at, textarea.current?.selectionStart ?? caret));
    onSkillChange?.(command);
    slash.reset();
    placeCaret(at.start);
  };

  const insertMention = () => {
    const element = textarea.current;
    const position = element?.selectionStart ?? value.length;
    const before = value.slice(0, position);
    const spacer = before && !/\s$/.test(before) ? " " : "";
    onValueChange(`${before}${spacer}@${value.slice(position)}`);
    libraries.reset();
    placeCaret(position + spacer.length + 1);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (slash.keyDown(event) || libraries.onKeyDown(event, (base) => selectLibrary(base))) return;
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (onAsk && value.trim()) { dictation.discard(); onAsk(); }
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (asks) { dictation.discard(); onAsk?.(); return; }
      if (canSubmit) { dictation.discard(); onSubmit(); }
      return;
    }
    if (event.key === "Escape" && (value || mentionIds.length || skill)) {
      event.preventDefault();
      onClear();
      return;
    }
    const element = event.currentTarget;
    if (event.key === "ArrowDown" && !event.shiftKey && onArrowDown && element.selectionStart === element.value.length && onArrowDown()) {
      event.preventDefault();
      return;
    }
    if (event.key === "Backspace" && (skill || mentionIds.length) && element.selectionStart === 0 && element.selectionEnd === 0) {
      event.preventDefault();
      // The chip nearest the text goes first.
      if (skill) onSkillChange?.(null);
      else onMentionsChange(mentionIds.slice(0, -1));
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (asks) { dictation.discard(); onAsk?.(); return; }
    if (canSubmit) { dictation.discard(); onSubmit(); }
  };

  const hint = skill
    ? `/${skill} is on: ↵ to ask · ⇧↵ for a new line`
    : opensLibrary
    ? `Press ↵ to open ${mentioned[0]!.title}, or type to search inside it`
    : mentioned.length
      ? `Searching ${mentioned.length === 1 ? mentioned[0]!.title : `${mentioned.length} libraries`}`
      : value.trim()
        ? onAsk ? "↵ to search · ⌘↵ to ask · ⇧↵ for a new line" : "↵ to search · ⇧↵ for a new line"
        : "";

  return (
    <form className={`gx-composer ${menuOpen ? "is-menu-open" : ""} ${dictating ? "is-dictating" : ""}`} role="search" onSubmit={submit}>
      <div className="gx-composer-field" onMouseDown={(event) => { if (event.target === event.currentTarget) { event.preventDefault(); textarea.current?.focus(); } }}>
        {mentioned.map((base) => (
          <span className="gx-mention" key={base.id}>
            <LibraryGlyph base={base} size="xs" />
            <span>{base.title}</span>
            <button type="button" onClick={() => { onMentionsChange(mentionIds.filter((id) => id !== base.id)); textarea.current?.focus(); }} aria-label={`Remove ${base.title}`}>
              <X size={11} />
            </button>
          </span>
        ))}
        {skill && <SkillChip command={skill} {...(budget ? { budget } : {})} {...(onBudgetChange ? { onBudgetChange } : {})} onRemove={() => { onSkillChange?.(null); textarea.current?.focus(); }} />}
        <textarea
          ref={textarea}
          rows={1}
          value={value}
          autoFocus
          spellCheck={false}
          placeholder={mentioned.length ? `Search in ${mentioned.length === 1 ? mentioned[0]!.title : "these libraries"}…` : placeholder}
          aria-label="Search your knowledge or the web"
          aria-autocomplete="list"
          aria-controls={librariesOpen ? libraries.listId : slash.open ? slash.listId : undefined}
          aria-activedescendant={activeOption ? libraries.optionId(activeOption.id) : slash.activeId}
          aria-describedby={hintId}
          onChange={(event) => { onValueChange(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
          onKeyDown={handleKeyDown}
          onBlur={() => { libraries.dismiss(); slash.dismiss(); }}
          onFocus={() => { dictation.claim(); libraries.reset(); slash.reset(); }}
        />
      </div>

      <div className="gx-composer-toolbar">
        <button type="button" className="gx-tool" onClick={insertMention} title="Choose a library (@)" aria-label="Choose a library">
          <AtSign size={15} />
          <span>Library</span>
        </button>
        <button type="button" className={`gx-tool ${web ? "is-on" : ""}`} onClick={() => onWebChange(!web)} aria-pressed={web} title={web ? "Web results are included" : "Include web results"}>
          <Globe2 size={15} />
          <span>Web</span>
        </button>
        {picker}
        <span className="gx-composer-hint" id={hintId} role={dictation.error ? "alert" : undefined}>{dictation.error ?? (dictating ? <DictationStatus state={dictation.state} level={dictation.level} /> : hint)}</span>
        <button type="button" className={`gx-tool gx-tool-icon gx-mic ${dictating ? "is-on" : ""} is-${dictation.state}`} onClick={dictation.toggle} aria-pressed={listening} aria-label={listening ? "Stop voice input" : "Voice input"} title={withShortcut(listening ? "Stop voice input" : "Speak instead of typing", "dictate")}>
          <MicGlyph state={dictation.state} />
        </button>
        {(value || mentionIds.length > 0 || skill) && (
          <button type="button" className="gx-tool gx-tool-icon" onClick={() => { onClear(); textarea.current?.focus(); }} aria-label="Clear search" title="Clear">
            <X size={15} />
          </button>
        )}
        <button type="submit" className={`gx-send ${searching ? "is-busy" : ""}`} disabled={!canSubmit} aria-label={opensLibrary ? "Open library" : asks ? "Ask" : "Run search"} title={opensLibrary ? "Open library" : asks ? "Ask (↵)" : "Search (↵)"}>
          {searching ? <span className="gx-spinner" aria-hidden="true" /> : opensLibrary ? <CornerDownLeft size={15} /> : <ArrowUp size={16} />}
        </button>
      </div>

      <TriggerMenu
        open={librariesOpen}
        listId={libraries.listId}
        label="Libraries"
        announce={`${options.length} ${options.length === 1 ? "library" : "libraries"} available`}
        empty={options.length === 0 ? <div className="gx-mention-empty">{bases.length ? `No library matches “${trigger?.query}”` : "No libraries yet. Create one from Libraries."}</div> : undefined}
      >
        {options.map((base, index) => (
          <div
            key={base.id}
            id={libraries.optionId(base.id)}
            role="option"
            aria-label={`${base.title}, ${sourceCount(base)}`}
            aria-selected={activeOption?.id === base.id}
            className={`gx-mention-option ${activeOption?.id === base.id ? "is-active" : ""}`}
            onMouseDown={(event) => { event.preventDefault(); selectLibrary(base); }}
            onMouseEnter={() => libraries.setActiveIndex(index)}
          >
            <LibraryGlyph base={base} size="sm" />
            <span className="gx-mention-title"><MatchedTitle title={base.title} query={trigger?.query ?? ""} /></span>
            <small>{sourceCount(base)}</small>
          </div>
        ))}
      </TriggerMenu>
      {slash.menu}
    </form>
  );
});
