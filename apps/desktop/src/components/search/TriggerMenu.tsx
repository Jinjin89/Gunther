import { useEffect, useId, useState, type KeyboardEvent, type ReactNode } from "react";

/** Where a trigger character (`@`, `/`) is, and what has been typed after it up to the caret. */
export interface Trigger {
  start: number;
  query: string;
}

const MAX_QUERY = 40;

/**
 * A trigger character up to the caret opens a menu. `@` must not continue an email-like ASCII word
 * ("me@lab.org") but may follow CJK text directly; `/` (with `atStart`) only counts as the first
 * character of the text.
 */
export const findTrigger = (text: string, caret: number, char: string, atStart = false): Trigger | null => {
  const before = text.slice(0, caret);
  const start = atStart ? (before.startsWith(char) ? 0 : -1) : before.lastIndexOf(char);
  if (start < 0) return null;
  if (!atStart && start > 0 && /[A-Za-z0-9._%+-]/.test(before[start - 1] ?? "")) return null;
  const query = before.slice(start + 1);
  if (query.length > MAX_QUERY || /[\n@]/.test(query)) return null;
  return { start, query };
};

/** The text with the typed trigger and query taken out (and the space after it). */
export const withoutTrigger = (text: string, trigger: Trigger, caret: number) =>
  text.slice(0, trigger.start) + text.slice(caret).replace(/^ /, "");

interface TriggerMenuOptions<T> {
  text: string;
  caret: number;
  char: string;
  atStart?: boolean;
  /** The options for what has been typed, best first. */
  options: (query: string) => T[];
}

/**
 * The state of one menu opened by a trigger character: its options, the active one, whether it is
 * open, and the keys that drive it (↑ ↓ move, Enter or Tab pick, Esc dismiss).
 */
export function useTriggerMenu<T extends { id: string }>({ text, caret, char, atStart = false, options: find }: TriggerMenuOptions<T>) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [dismissedStart, setDismissedStart] = useState<number | null>(null);
  const listId = useId();
  const trigger = findTrigger(text, caret, char, atStart);
  const options = trigger ? find(trigger.query) : [];
  const open = Boolean(trigger)
    && trigger!.start !== dismissedStart
    && (options.length > 0 || !trigger!.query.includes(" "));
  const active = open ? options[Math.min(activeIndex, options.length - 1)] : undefined;

  useEffect(() => { setActiveIndex(0); }, [trigger?.query, trigger?.start]);

  /** Returns true when the key was the menu's. */
  const onKeyDown = (event: KeyboardEvent, pick: (option: T, trigger: Trigger) => void) => {
    if (!open || !trigger) return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!options.length) return true;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => (current + step + options.length) % options.length);
      return true;
    }
    if ((event.key === "Enter" || event.key === "Tab") && active) {
      event.preventDefault();
      pick(active, trigger);
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setDismissedStart(trigger.start);
      return true;
    }
    return false;
  };

  return {
    trigger,
    options,
    open,
    active,
    setActiveIndex,
    listId,
    optionId: (id: string) => `${listId}-${id}`,
    onKeyDown,
    /** Close the menu for the trigger that is open now (a blur, or an Esc by other means). */
    dismiss: () => setDismissedStart(trigger?.start ?? null),
    /** Let the menu open again. */
    reset: () => setDismissedStart(null),
  };
}

export function MatchedTitle({ title, query }: { title: string; query: string }) {
  const needle = query.trim();
  const index = needle ? title.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (index < 0) return <>{title}</>;
  return <>{title.slice(0, index)}<b>{title.slice(index, index + needle.length)}</b>{title.slice(index + needle.length)}</>;
}

/** The listbox a trigger opens: a label, the options as children, a key hint, and a polite count for screen readers. */
export function TriggerMenu({ open, listId, label, announce, empty, children }: {
  open: boolean;
  listId: string;
  label: string;
  /** What a screen reader hears when the menu changes ("3 libraries available"). */
  announce: string;
  /** Shown when no option matches. */
  empty?: ReactNode;
  children: ReactNode;
}) {
  return <>
    {open && (
      <div className="gx-mention-menu" role="listbox" id={listId} aria-label={label}>
        <div className="gx-menu-label" aria-hidden="true">{label}</div>
        {children}
        {empty}
        <div className="gx-menu-footer" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> choose <kbd>esc</kbd> close</div>
      </div>
    )}
    <span className="gx-sr-only" aria-live="polite">{open ? announce : ""}</span>
  </>;
}
