import { Check, ChevronsUpDown, Inbox, Plus, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { KnowledgeBase } from "../atlas";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { useEscape } from "../shortcuts/shortcuts";

interface LibraryPickerProps {
  bases: KnowledgeBase[];
  value: string;
  onChange: (id: string) => void;
  onCreate?: (() => void) | undefined;
  disabled?: boolean;
  label?: string;
  /** Open upwards, for pickers near the bottom of a panel. */
  placement?: "below" | "above";
  /** Offer "no library" (value "") first, labelled like "Inbox". */
  noneLabel?: string;
}

/** Choose a library by its identity glyph and name; filters as you type. */
export function LibraryPicker({ bases, value, onChange, onCreate, disabled = false, label = "Library", placement = "below", noneLabel }: LibraryPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const filterInput = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();
  const selected = bases.find((base) => base.id === value) ?? null;
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = needle ? bases.filter((base) => `${base.title} ${base.eyebrow}`.toLowerCase().includes(needle)) : bases;
    // "No library" is listed first as a pseudo-library with an empty id.
    return noneLabel && (!needle || noneLabel.toLowerCase().includes(needle)) ? [null, ...matches] : matches;
  }, [bases, noneLabel, query]);
  const options = onCreate ? filtered.length + 1 : filtered.length;
  const showFilter = bases.length > 6;

  const close = (refocus = true) => {
    setOpen(false);
    setQuery("");
    if (refocus) trigger.current?.focus();
  };
  useEscape(() => close(), open);

  useEffect(() => {
    if (!open) return undefined;
    setActive(Math.max(0, filtered.findIndex((base) => (base?.id ?? "") === value)));
    // Focus moves into the menu so arrow keys and screen readers follow the active option.
    window.requestAnimationFrame(() => (showFilter ? filterInput.current : list.current)?.focus());
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close(false);
    };
    window.addEventListener("pointerdown", onPointer);
    return () => window.removeEventListener("pointerdown", onPointer);
    // Only when the menu opens.
  }, [open]);

  const choose = (index: number) => {
    if (index < filtered.length) {
      onChange(filtered[index]?.id ?? "");
      close();
    } else if (onCreate) {
      close(false);
      onCreate();
    }
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        setOpen(true);
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current) => (current + 1) % Math.max(1, options));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => (current - 1 + Math.max(1, options)) % Math.max(1, options));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(active);
    } else if (event.key === "Tab") {
      close(false);
    }
  };

  const optionId = (index: number) => `${listId}-option-${index}`;

  return (
    <div className={`gx-picker ${open ? "is-open" : ""} opens-${placement}`} ref={root} onKeyDown={onKeyDown}>
      <button
        ref={trigger}
        type="button"
        className="gx-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${selected?.title ?? noneLabel ?? "none"}`}
        disabled={disabled || (!bases.length && !onCreate && !noneLabel)}
        onClick={() => setOpen((current) => !current)}
      >
        {selected ? <LibraryGlyph base={selected} size="sm" /> : noneLabel ? <span className="gx-picker-inbox"><Inbox size={12} /></span> : <span className="gx-picker-empty-glyph" />}
        <span className="gx-picker-value">{selected?.title ?? noneLabel ?? (bases.length ? "Choose a library" : "No libraries yet")}</span>
        <ChevronsUpDown size={14} className="gx-picker-chevron" />
      </button>
      {open && (
        <div className="gx-picker-menu" role="presentation">
          {showFilter && (
            <label className="gx-picker-filter">
              <Search size={13} />
              <input
                ref={filterInput}
                value={query}
                onChange={(event) => { setQuery(event.target.value); setActive(0); }}
                placeholder="Find a library"
                aria-label="Find a library"
                aria-controls={listId}
                aria-activedescendant={options ? optionId(active) : undefined}
              />
            </label>
          )}
          <div ref={list} className="gx-picker-list" role="listbox" id={listId} aria-label={label} tabIndex={showFilter ? -1 : 0} aria-activedescendant={options ? optionId(active) : undefined}>
            {filtered.map((base, index) => (
              <div
                key={base?.id ?? "none"}
                id={optionId(index)}
                role="option"
                aria-selected={(base?.id ?? "") === value}
                className={`gx-picker-option ${index === active ? "is-active" : ""}`}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(index)}
              >
                {base ? <LibraryGlyph base={base} size="sm" /> : <span className="gx-picker-inbox"><Inbox size={12} /></span>}
                <span>
                  <strong>{base?.title ?? noneLabel}</strong>
                  <small>{base ? `${base.indexedSourceCount ?? base.sourceCount} source${(base.indexedSourceCount ?? base.sourceCount) === 1 ? "" : "s"}` : "Decide where it belongs later"}</small>
                </span>
                {(base?.id ?? "") === value && <Check size={14} className="gx-picker-check" />}
              </div>
            ))}
            {!filtered.length && <p className="gx-picker-none">No library matches “{query}”.</p>}
            {onCreate && (
              <div
                id={optionId(filtered.length)}
                role="option"
                aria-selected={false}
                className={`gx-picker-option is-create ${active === filtered.length ? "is-active" : ""}`}
                onMouseEnter={() => setActive(filtered.length)}
                onClick={() => choose(filtered.length)}
              >
                <span className="gx-picker-plus"><Plus size={13} /></span>
                <span><strong>New library…</strong></span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
