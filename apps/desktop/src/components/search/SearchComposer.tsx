import { ArrowUp, AtSign, CornerDownLeft, Globe2, Mic, X } from "lucide-react";
import { forwardRef, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import type { KnowledgeBase } from "../../atlas";
import { LibraryGlyph } from "../../design/LibraryGlyph";
import { useDictation } from "../../services/useDictation";

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
}

interface MentionTrigger {
  start: number;
  query: string;
}

const MAX_MENTION_QUERY = 40;
const MAX_OPTIONS = 8;
const MAX_TEXTAREA_HEIGHT = 168;

/**
 * An `@` up to the caret opens library suggestions. It must not continue an
 * email-like ASCII word ("me@lab.org"), but may follow CJK text directly.
 */
const findTrigger = (text: string, caret: number): MentionTrigger | null => {
  const before = text.slice(0, caret);
  const start = before.lastIndexOf("@");
  if (start < 0) return null;
  if (start > 0 && /[A-Za-z0-9._%+-]/.test(before[start - 1] ?? "")) return null;
  const query = before.slice(start + 1);
  if (query.length > MAX_MENTION_QUERY || /[\n@]/.test(query)) return null;
  return { start, query };
};

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

function MatchedTitle({ title, query }: { title: string; query: string }) {
  const needle = query.trim();
  const index = needle ? title.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (index < 0) return <>{title}</>;
  return <>{title.slice(0, index)}<b>{title.slice(index, index + needle.length)}</b>{title.slice(index + needle.length)}</>;
}

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
}, ref) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [dismissedStart, setDismissedStart] = useState<number | null>(null);
  const pendingCaret = useRef<number | null>(null);
  const listId = useId();
  const hintId = useId();

  const latest = useRef(value);
  latest.current = value;
  const dictation = useDictation((text) => {
    const before = latest.current;
    const spacer = before && !/\s$/.test(before) && !/^[\u3000-\u9fff\uff00-\uffef]/.test(text) ? " " : "";
    latest.current = `${before}${spacer}${text}`;
    onValueChange(latest.current);
    pendingCaret.current = latest.current.length;
  });
  const listening = dictation.state === "listening" || dictation.state === "starting";
  const dictating = dictation.state !== "idle";

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

  const mentioned = useMemo(() => mentionIds.map((id) => bases.find((base) => base.id === id)).filter((base): base is KnowledgeBase => Boolean(base)), [bases, mentionIds]);
  const trigger = findTrigger(value, caret);
  const options = trigger ? matchLibraries(bases, mentionIds, trigger.query) : [];
  const menuOpen = Boolean(trigger)
    && trigger!.start !== dismissedStart
    && (options.length > 0 || !trigger!.query.includes(" "));
  const activeOption = menuOpen ? options[Math.min(activeIndex, options.length - 1)] : undefined;
  const opensLibrary = !value.trim() && mentioned.length === 1;
  const canSubmit = Boolean(value.trim()) || opensLibrary;

  useEffect(() => { setActiveIndex(0); }, [trigger?.query, trigger?.start]);

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
    const end = textarea.current?.selectionStart ?? caret;
    const next = value.slice(0, trigger.start) + value.slice(end).replace(/^ /, "");
    onValueChange(next);
    onMentionsChange([...mentionIds, base.id]);
    setDismissedStart(null);
    placeCaret(trigger.start);
  };

  const insertMention = () => {
    const element = textarea.current;
    const position = element?.selectionStart ?? value.length;
    const before = value.slice(0, position);
    const spacer = before && !/\s$/.test(before) ? " " : "";
    onValueChange(`${before}${spacer}@${value.slice(position)}`);
    setDismissedStart(null);
    placeCaret(position + spacer.length + 1);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (menuOpen) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!options.length) return;
        const step = event.key === "ArrowDown" ? 1 : -1;
        setActiveIndex((current) => (current + step + options.length) % options.length);
        return;
      }
      if ((event.key === "Enter" || event.key === "Tab") && activeOption) {
        event.preventDefault();
        selectLibrary(activeOption);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setDismissedStart(trigger?.start ?? null);
        return;
      }
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (onAsk && value.trim()) onAsk();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSubmit) onSubmit();
      return;
    }
    if (event.key === "Escape" && (value || mentionIds.length)) {
      event.preventDefault();
      onClear();
      return;
    }
    const element = event.currentTarget;
    if (event.key === "ArrowDown" && !event.shiftKey && onArrowDown && element.selectionStart === element.value.length && onArrowDown()) {
      event.preventDefault();
      return;
    }
    if (event.key === "Backspace" && mentionIds.length && element.selectionStart === 0 && element.selectionEnd === 0) {
      event.preventDefault();
      onMentionsChange(mentionIds.slice(0, -1));
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (canSubmit) onSubmit();
  };

  const hint = opensLibrary
    ? `Press ↵ to open ${mentioned[0]!.title}, or type to search inside it`
    : mentioned.length
      ? `Searching ${mentioned.length === 1 ? mentioned[0]!.title : `${mentioned.length} libraries`}`
      : value.trim()
        ? onAsk ? "↵ to search · ⌘↵ to ask · ⇧↵ for a new line" : "↵ to search · ⇧↵ for a new line"
        : "";

  return (
    <form className={`gx-composer ${menuOpen ? "is-menu-open" : ""}`} role="search" onSubmit={submit}>
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
        <textarea
          ref={textarea}
          rows={1}
          value={value}
          autoFocus
          spellCheck={false}
          placeholder={mentioned.length ? `Search in ${mentioned.length === 1 ? mentioned[0]!.title : "these libraries"}…` : placeholder}
          aria-label="Search your knowledge or the web"
          aria-autocomplete="list"
          aria-controls={menuOpen ? listId : undefined}
          aria-activedescendant={activeOption ? `${listId}-${activeOption.id}` : undefined}
          aria-describedby={hintId}
          onChange={(event) => { onValueChange(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
          onKeyDown={handleKeyDown}
          onBlur={() => setDismissedStart(trigger?.start ?? null)}
          onFocus={() => setDismissedStart(null)}
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
        <span className="gx-composer-hint" id={hintId} role={dictation.error ? "alert" : undefined}>{dictation.error ?? (dictating ? (dictation.state === "finishing" ? "Finishing…" : "Listening… click the mic to stop") : hint)}</span>
        <button type="button" className={`gx-tool gx-tool-icon gx-mic ${dictating ? "is-on" : ""}`} onClick={() => (listening ? dictation.stop() : dictation.state === "idle" ? void dictation.start() : undefined)} aria-pressed={listening} aria-label={listening ? "Stop voice input" : "Voice input"} title={listening ? "Stop voice input" : "Speak instead of typing"}>
          <Mic size={15} />
        </button>
        {(value || mentionIds.length > 0) && (
          <button type="button" className="gx-tool gx-tool-icon" onClick={() => { onClear(); textarea.current?.focus(); }} aria-label="Clear search" title="Clear">
            <X size={15} />
          </button>
        )}
        <button type="submit" className={`gx-send ${searching ? "is-busy" : ""}`} disabled={!canSubmit} aria-label={opensLibrary ? "Open library" : "Run search"} title={opensLibrary ? "Open library" : "Search (↵)"}>
          {searching ? <span className="gx-spinner" aria-hidden="true" /> : opensLibrary ? <CornerDownLeft size={15} /> : <ArrowUp size={16} />}
        </button>
      </div>

      {menuOpen && (
        <div className="gx-mention-menu" role="listbox" id={listId} aria-label="Libraries">
          <div className="gx-menu-label" aria-hidden="true">Libraries</div>
          {options.map((base, index) => (
            <div
              key={base.id}
              id={`${listId}-${base.id}`}
              role="option"
              aria-label={`${base.title}, ${sourceCount(base)}`}
              aria-selected={activeOption?.id === base.id}
              className={`gx-mention-option ${activeOption?.id === base.id ? "is-active" : ""}`}
              onMouseDown={(event) => { event.preventDefault(); selectLibrary(base); }}
              onMouseEnter={() => setActiveIndex(index)}
            >
              <LibraryGlyph base={base} size="sm" />
              <span className="gx-mention-title"><MatchedTitle title={base.title} query={trigger?.query ?? ""} /></span>
              <small>{sourceCount(base)}</small>
            </div>
          ))}
          {options.length === 0 && (
            <div className="gx-mention-empty">{bases.length ? `No library matches “${trigger?.query}”` : "No libraries yet. Create one from Libraries."}</div>
          )}
          <div className="gx-menu-footer" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> choose <kbd>esc</kbd> close</div>
        </div>
      )}
      <span className="gx-sr-only" aria-live="polite">{menuOpen ? `${options.length} ${options.length === 1 ? "library" : "libraries"} available` : ""}</span>
    </form>
  );
});
