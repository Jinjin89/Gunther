import type { Effort, ModelChoice, ModelMenu } from "@gunther/contracts";
import { EFFORTS } from "@gunther/contracts";
import { Brain, Check, ChevronDown, Eye, Settings2, Sparkles } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { EFFORT_NAMES, OPEN_SETTINGS_EVENT, resolveEffort, type AskChoice } from "./askModel";

interface ModelPickerProps {
  menu: ModelMenu | null;
  choice: AskChoice;
  onChange: (choice: AskChoice) => void;
  disabled?: boolean;
  /** Menus open above a composer at the bottom of the page, below one at the top. */
  placement?: "up" | "down";
}

const openSettings = () => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT));

/** Move focus through a menu's items with the arrow keys, Home and End. */
function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("[role^=menuitem]:not(:disabled)")];
  const at = items.indexOf(document.activeElement as HTMLButtonElement);
  const next = event.key === "ArrowDown" ? at + 1 : event.key === "ArrowUp" ? at - 1 : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : null;
  if (next === null || !items.length) return;
  event.preventDefault();
  items[(next + items.length) % items.length]?.focus();
}

function Popover({ id, label, placement, onClose, children }: { id: string; label: string; placement: "up" | "down"; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    (element?.querySelector<HTMLButtonElement>("[aria-checked=true]") ?? element?.querySelector<HTMLButtonElement>("[role^=menuitem]"))?.focus();
  }, []);
  return <div
    ref={ref}
    id={id}
    className={`gx-model-menu is-${placement}`}
    role="menu"
    aria-label={label}
    onKeyDown={(event) => {
      if (event.key === "Escape" || event.key === "Tab") {
        if (event.key === "Escape") event.preventDefault();
        event.stopPropagation();
        onClose();
        return;
      }
      moveFocus(event);
    }}
  >{children}</div>;
}

function groups(models: ModelChoice[]) {
  const byProvider = new Map<string, ModelChoice[]>();
  for (const model of models) byProvider.set(model.provider, [...(byProvider.get(model.provider) ?? []), model]);
  return [...byProvider.entries()];
}

/** Which model answers, and how hard it thinks. The same two controls wherever you ask. */
export function ModelPicker({ menu, choice, onChange, disabled = false, placement = "up" }: ModelPickerProps) {
  const [open, setOpen] = useState<"model" | "effort" | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const modelMenuId = useId();
  const effortMenuId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(null);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!menu) return null;
  if (!menu.models.length) {
    return <div className="gx-model-picker">
      <button type="button" className="gx-model-chip is-empty" onClick={openSettings} title="Add a provider and a key in Settings → Models">
        <Sparkles size={13} /><span>Set up a model</span>
      </button>
    </div>;
  }

  const current = menu.models.find((model) => model.ref === choice.model) ?? menu.models[0]!;
  const applied = resolveEffort(choice.effort, current.levels);
  const adjusted = applied && applied.id !== choice.effort;
  const close = (focus?: "model" | "effort") => {
    setOpen(null);
    if (focus) root.current?.querySelector<HTMLButtonElement>(`[data-chip=${focus}]`)?.focus();
  };
  const effortTitle = !applied
    ? `${current.label} has no effort setting; it thinks as it always does.`
    : adjusted
      ? `${current.label} has no ${EFFORT_NAMES[choice.effort]} setting; it uses ${applied.label}.`
      : `Thinking effort: ${applied.label}`;

  return <div className="gx-model-picker" ref={root}>
    <button
      type="button"
      data-chip="model"
      className={`gx-model-chip ${open === "model" ? "is-open" : ""}`}
      disabled={disabled}
      aria-haspopup="menu"
      aria-expanded={open === "model"}
      aria-controls={open === "model" ? modelMenuId : undefined}
      aria-label={`Model: ${current.display}`}
      title={current.display}
      onClick={() => setOpen(open === "model" ? null : "model")}
    >
      <Sparkles size={13} /><span className="gx-model-chip-provider">{current.provider}</span><span>{current.label}</span><ChevronDown size={11} />
    </button>
    <button
      type="button"
      data-chip="effort"
      className={`gx-model-chip ${open === "effort" ? "is-open" : ""} ${applied ? "" : "is-fixed"}`}
      disabled={disabled || !applied}
      aria-haspopup="menu"
      aria-expanded={open === "effort"}
      aria-controls={open === "effort" ? effortMenuId : undefined}
      aria-label={applied ? `Thinking effort: ${applied.label}` : "Thinking effort: fixed"}
      title={effortTitle}
      onClick={() => setOpen(open === "effort" ? null : "effort")}
    >
      <Brain size={13} /><span>{applied ? applied.label : "Fixed"}</span>{applied && <ChevronDown size={11} />}
    </button>

    {open === "model" && <Popover id={modelMenuId} label="Model" placement={placement} onClose={() => close("model")}>
      {groups(menu.models).map(([provider, models]) => <div className="gx-model-group" key={provider} role="group" aria-label={provider}>
        <div className="gx-menu-label" aria-hidden="true">{provider}</div>
        {models.map((model) => <button
          key={model.ref}
          type="button"
          role="menuitemradio"
          aria-checked={model.ref === current.ref}
          className="gx-model-option"
          onClick={() => { onChange({ ...choice, model: model.ref }); close("model"); }}
        >
          <span>{model.label}</span>
          {model.vision && <Eye size={12} aria-label="Sees images" />}
          {!model.levels.length && <small>no effort setting</small>}
          {model.ref === current.ref && <Check size={13} className="gx-model-check" />}
        </button>)}
      </div>)}
      <button type="button" role="menuitem" className="gx-model-option is-footer" onClick={() => { close(); openSettings(); }}>
        <Settings2 size={13} /><span>Manage models…</span>
      </button>
    </Popover>}

    {open === "effort" && applied && <Popover id={effortMenuId} label="Thinking effort" placement={placement} onClose={() => close("effort")}>
      <div className="gx-menu-label" aria-hidden="true">Thinking effort · {current.label}</div>
      {EFFORTS.map((effort: Effort) => {
        const used = resolveEffort(effort, current.levels);
        const exact = used?.id === effort;
        return <button
          key={effort}
          type="button"
          role="menuitemradio"
          aria-checked={choice.effort === effort}
          className={`gx-model-option ${exact ? "" : "is-mapped"}`}
          onClick={() => { onChange({ ...choice, effort }); close("effort"); }}
        >
          <span>{EFFORT_NAMES[effort]}</span>
          {!exact && used && <small>{effort === "off" ? "always thinks" : `uses ${used.label}`}</small>}
          {choice.effort === effort && <Check size={13} className="gx-model-check" />}
        </button>;
      })}
    </Popover>}
  </div>;
}
