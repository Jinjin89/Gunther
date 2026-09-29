import type { ServiceField, ServiceSettings as Service, ServiceSettingValue, ServiceState, ServiceTestResult } from "@gunther/contracts";
import { AudioLines, Check, ChevronRight, CircleAlert, Eye, EyeOff, FileText, Globe, KeyRound, Plug, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import { knowledgeApi } from "../api";
import { MODELS_CHANGED_EVENT } from "../models/askModel";

type Draft = Record<string, ServiceSettingValue>;
type Values = Record<string, ServiceSettingValue>;

const SERVICE_ICONS: Record<string, ReactNode> = {
  openai: <Globe size={15} />,
  transcription: <AudioLines size={15} />,
  summaries: <FileText size={15} />,
};

const STATE_LABELS: Record<ServiceState, string> = {
  // "Connected" only once a test of these values passed.
  configured: "Set up",
  not_configured: "Not set up",
  error: "Needs attention",
  off: "Off",
};

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** The values a service's form shows: what is in use, with unsaved changes on top. */
const currentValues = (service: Service, draft: Draft): Values =>
  Object.fromEntries(service.fields.map((field) => [field.key, field.key in draft ? draft[field.key] ?? null : field.value]));

const isShown = (field: ServiceField, values: Values) =>
  !field.shownWhen || field.shownWhen.values.includes(String(values[field.shownWhen.key]));

/** What is wrong with a value, in the words the service would use; null when it can be saved. */
export function fieldProblem(field: ServiceField, value: ServiceSettingValue): string | null {
  if (field.kind === "secret" || field.kind === "toggle" || field.kind === "select") return null;
  const text = value === null || value === undefined ? "" : String(value).trim();
  if (!text) return field.required ? "This cannot be empty." : null;
  if (field.kind === "url") {
    try {
      const url = new URL(text);
      if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("scheme");
    } catch {
      return "Use a full address starting with http:// or https://.";
    }
  }
  if (field.kind === "number") {
    const number = Number(text);
    if (!Number.isFinite(number)) return "Enter a number.";
    if ((field.min !== null && number < field.min) || (field.max !== null && number > field.max)) return `Use a number from ${field.min} to ${field.max}.`;
  }
  if (field.pattern && !new RegExp(`^(?:${field.pattern})$`).test(text)) return field.patternMessage || "This value is not supported.";
  return null;
}

/** Picking an option (a provider) fills in its defaults, unless someone typed their own. */
function applyPresets(field: ServiceField, choice: string, values: Values): Draft {
  const chosen = field.options.find((option) => option.value === choice);
  const next: Draft = { [field.key]: choice };
  if (!chosen) return next;
  for (const [key, preset] of Object.entries(chosen.presets)) {
    const current = values[key];
    const untouched = current === null || current === "" || field.options.some((option) => option.presets[key] === current);
    if (untouched) next[key] = preset;
  }
  return next;
}

function StatusBadge({ service }: { service: Service }) {
  const { state, check } = service.status;
  // A passing test of the values in use says more than "Ready".
  const label = state === "configured" && check?.ok ? "Connected" : STATE_LABELS[state];
  return <span className={`service-badge is-${state}`}><i aria-hidden="true" />{label}</span>;
}

function SecretField({ field, value, onChange, inputId, describedBy }: {
  field: ServiceField;
  value: ServiceSettingValue | undefined;
  onChange: (value: ServiceSettingValue | undefined) => void;
  inputId: string;
  describedBy: string;
}) {
  const [visible, setVisible] = useState(false);
  const removing = value === "";
  const editing = typeof value === "string" && value !== "";
  const [replacing, setReplacing] = useState(false);
  // A newly saved or removed key closes the editor.
  useEffect(() => setReplacing(false), [field.hint, field.isSet]);

  if (field.isSet && !replacing && !editing) {
    return <div className="service-secret-saved">
      <span className={`service-secret-mask ${removing ? "is-removing" : ""}`}><KeyRound size={13} aria-hidden="true" />{removing ? "Removed when you save" : `•••• ${field.hint ?? "saved"}`}</span>
      {removing
        ? <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChange(undefined)}>Undo</button>
        : <>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setReplacing(true)}>Replace</button>
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChange("")}>Remove</button>
        </>}
    </div>;
  }

  return <div className="service-secret-input">
    <input
      id={inputId}
      type={visible ? "text" : "password"}
      value={typeof value === "string" ? value : ""}
      placeholder={field.isSet ? "Paste a new key" : field.placeholder || "Paste your key"}
      autoComplete="off"
      spellCheck={false}
      autoFocus={replacing}
      aria-describedby={describedBy}
      onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
    />
    <button type="button" className="gx-icon-button" aria-label={visible ? `Hide ${field.label}` : `Show ${field.label}`} title={visible ? "Hide" : "Show"} onClick={() => setVisible((current) => !current)}>
      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
    </button>
    {replacing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setReplacing(false); onChange(undefined); }}>Cancel</button>}
  </div>;
}

function FieldControl({ field, value, draftValue, onChange, inputId, describedBy, invalid }: {
  field: ServiceField;
  value: ServiceSettingValue;
  draftValue: ServiceSettingValue | undefined;
  onChange: (value: ServiceSettingValue | undefined) => void;
  inputId: string;
  describedBy: string;
  invalid: boolean;
}) {
  if (field.kind === "secret") return <SecretField field={field} value={draftValue} onChange={onChange} inputId={inputId} describedBy={describedBy} />;
  if (field.kind === "toggle") {
    return <label className="service-switch">
      <input id={inputId} type="checkbox" role="switch" checked={Boolean(value)} aria-describedby={describedBy} onChange={(event) => onChange(event.target.checked)} />
      <i aria-hidden="true" />
    </label>;
  }
  if (field.kind === "select") {
    return <div className="service-segmented" role="radiogroup" aria-labelledby={`${inputId}-label`} aria-describedby={describedBy}>
      {field.options.map((option) => <button
        key={option.value}
        type="button"
        role="radio"
        aria-checked={value === option.value}
        className={value === option.value ? "is-selected" : ""}
        title={option.description || undefined}
        onClick={() => onChange(option.value)}
      >{option.label}</button>)}
    </div>;
  }
  return <input
    id={inputId}
    className={field.kind === "url" ? "is-mono" : ""}
    type={field.kind === "number" ? "number" : field.kind === "url" ? "url" : "text"}
    inputMode={field.kind === "number" ? "decimal" : undefined}
    min={field.min ?? undefined}
    max={field.max ?? undefined}
    step={field.step ?? undefined}
    value={value === null || value === undefined ? "" : String(value)}
    placeholder={field.placeholder || (field.default !== null ? String(field.default) : "")}
    spellCheck={false}
    autoComplete="off"
    aria-invalid={invalid || undefined}
    aria-describedby={describedBy}
    onChange={(event) => onChange(field.kind === "number" && event.target.value !== "" ? Number(event.target.value) : event.target.value)}
  />;
}

function ServiceEditor({ service, persisted, onSaved, onNotify }: {
  service: Service;
  persisted: boolean;
  onSaved: (service: Service) => void;
  onNotify: (message: string) => void;
}) {
  const baseId = useId();
  const [draft, setDraft] = useState<Draft>({});
  const [showProblems, setShowProblems] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ServiceTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const values = currentValues(service, draft);
  const shown = service.fields.filter((field) => isShown(field, values));
  const problems = Object.fromEntries(shown.map((field) => [field.key, fieldProblem(field, values[field.key] ?? null)]).filter(([, problem]) => problem));
  const changed = Object.keys(draft).length > 0;
  const invalid = Object.keys(problems).length > 0;

  const change = (field: ServiceField, next: ServiceSettingValue | undefined) => {
    setResult(null);
    setError(null);
    setDraft((current) => {
      const updated = { ...current };
      const incoming: Draft = field.kind === "select" && typeof next === "string"
        ? applyPresets(field, next, currentValues(service, current))
        : next === undefined ? {} : { [field.key]: next };
      if (next === undefined) delete updated[field.key];
      for (const [key, value] of Object.entries(incoming)) {
        const original = service.fields.find((item) => item.key === key);
        // Back to what is in use: nothing to save for this field.
        if (original && original.kind !== "secret" && original.value === value) delete updated[key];
        else updated[key] = value;
      }
      return updated;
    });
  };

  const test = async (payload: Draft = draft) => {
    setTesting(true);
    setError(null);
    try {
      const tested = await knowledgeApi.testServiceSettings(service.id, payload);
      setResult(tested);
      if (Object.keys(payload).length === 0) onSaved(tested.service);
    } catch (reason) {
      setError(message(reason, "The connection could not be tested."));
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setShowProblems(true);
    if (invalid || !changed) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await knowledgeApi.saveServiceSettings(service.id, draft);
      setDraft({});
      setShowProblems(false);
      onSaved(saved);
      onNotify(`${service.title} saved.${persisted ? "" : " It lasts until the service restarts."}`);
      // Check what was just saved, so the status says whether it works.
      if (service.canTest && saved.status.state !== "not_configured" && saved.status.state !== "off") void test({});
    } catch (reason) {
      setError(message(reason, `${service.title} could not be saved.`));
    } finally {
      setSaving(false);
    }
  };

  const discard = () => {
    setDraft({});
    setShowProblems(false);
    setError(null);
    setResult(null);
  };

  const outcome = error
    ? <p className="service-outcome is-error" role="alert"><CircleAlert size={14} />{error}</p>
    : result
      ? <p className={`service-outcome ${result.ok ? result.warning ? "is-warning" : "is-ok" : "is-error"}`} role={result.ok ? "status" : "alert"}>
        {result.ok ? result.warning ? <TriangleAlert size={14} /> : <Check size={14} /> : <CircleAlert size={14} />}
        <span>{result.message}{result.warning && <small>{result.warning}</small>}</span>
      </p>
      : testing ? <p className="service-outcome" role="status"><span className="gx-spinner" aria-hidden="true" />Testing the connection…</p> : null;

  return <form className="service-editor" onSubmit={(event) => { event.preventDefault(); void save(); }} noValidate>
    <p className="service-editor-lead">{service.description}{service.note && <> {service.note}</>}</p>
    <div className="service-fields">
      {shown.map((field) => {
        const inputId = `${baseId}-${field.key}`;
        const helpId = `${inputId}-help`;
        const problem = showProblems || field.key in draft ? problems[field.key] : null;
        const selected = field.kind === "select" ? field.options.find((option) => option.value === values[field.key]) : undefined;
        const help = [selected?.description, field.help].filter(Boolean).join(" ");
        return <div className={`service-field ${problem ? "has-problem" : ""}`} key={field.key}>
          <label id={`${inputId}-label`} htmlFor={field.kind === "select" ? undefined : inputId}>
            {field.label}
            {field.source === "environment" && !(field.key in draft) && <span className="service-source" title={`Set as ${field.envVar} in the backend’s environment or .env file. Saving here takes its place.`}>from .env</span>}
          </label>
          <div className="service-control">
            <FieldControl field={field} value={values[field.key] ?? null} draftValue={draft[field.key]} onChange={(next) => change(field, next)} inputId={inputId} describedBy={helpId} invalid={Boolean(problem)} />
            <small id={helpId} className={problem ? "is-problem" : ""}>{problem ?? help}</small>
          </div>
        </div>;
      })}
    </div>
    <footer className="service-editor-footer">
      <div className="service-outcome-slot" aria-live="polite">{outcome}</div>
      <div className="service-actions">
        {service.canTest && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={testing || saving || invalid} onClick={() => void test()}><Plug size={13} />{testing ? "Testing…" : "Test connection"}</button>}
        {changed && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={saving} onClick={discard}>Discard</button>}
        <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={!changed || saving}>{saving ? "Saving…" : "Save"}</button>
      </div>
    </footer>
  </form>;
}

/** Keys, addresses and models for the outside services Gunther uses, drawn from what the service declares. */
export function ServiceSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const [services, setServices] = useState<Service[] | null>(null);
  const [persisted, setPersisted] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => void knowledgeApi.serviceSettings().then((list) => {
      if (!active) return;
      setServices(list.services);
      setPersisted(list.persisted);
    }).catch((reason) => {
      if (active) setProblem(message(reason, "Service settings could not be loaded."));
    });
    load();
    // Summaries follow the Analysis model: their status changes with Models.
    window.addEventListener(MODELS_CHANGED_EVENT, load);
    return () => { active = false; window.removeEventListener(MODELS_CHANGED_EVENT, load); };
  }, []);

  const replace = (next: Service) => setServices((current) => current?.map((service) => service.id === next.id ? next : service) ?? null);
  // Services lean on each other (transcription on OpenAI's key); refresh them all after a save.
  const saved = (next: Service) => {
    replace(next);
    void knowledgeApi.serviceSettings().then((list) => setServices(list.services)).catch(() => undefined);
  };

  return <section className="service-settings" aria-labelledby="service-settings-heading">
    <div className="setting-heading">
      <Plug size={16} />
      <span><strong id="service-settings-heading">Services</strong><small>Web search, transcription and summaries. Changes apply right away, and keys stay on this computer.</small></span>
    </div>
    {!services && !problem && <div className="setting-row"><span><strong>Loading…</strong><small>Asking the local service which services it uses.</small></span></div>}
    {problem && <div className="setting-row is-problem"><span><strong><CircleAlert size={13} />Unavailable</strong><small>{problem}</small></span></div>}
    {services && <ul className="service-list">
      {services.map((service) => {
        const expanded = open === service.id;
        const panelId = `service-panel-${service.id}`;
        return <li key={service.id} className={`service-item ${expanded ? "is-open" : ""}`}>
          <button type="button" className="service-summary" aria-expanded={expanded} aria-controls={panelId} onClick={() => setOpen(expanded ? null : service.id)}>
            <span className="service-icon" aria-hidden="true">{SERVICE_ICONS[service.id] ?? <Plug size={15} />}</span>
            <span className="service-heading"><strong>{service.title}</strong><small>{service.status.summary}</small></span>
            <StatusBadge service={service} />
            <ChevronRight size={15} className="service-chevron" aria-hidden="true" />
          </button>
          {expanded && <div id={panelId} className="service-panel">
            <ServiceEditor service={service} persisted={persisted} onSaved={saved} onNotify={onNotify} />
          </div>}
        </li>;
      })}
    </ul>}
    {services && !persisted && <p className="library-folder-note">This backend has no settings file, so changes last until it restarts.</p>}
  </section>;
}
