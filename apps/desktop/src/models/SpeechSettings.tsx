import type { SpeechOverview, SpeechProvider, SpeechRole, SpeechKind, SpeechTestResult } from "@gunther/contracts";
import { AudioLines, Check, ChevronRight, CircleAlert, Eye, EyeOff, KeyRound, Plug, Plus, RefreshCw, Server, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { knowledgeApi } from "../api";

const STATE_LABELS = { configured: "Set up", not_configured: "Not set up", error: "Needs attention", off: "Off" } as const;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

interface ProviderDraft {
  name: string;
  baseUrl: string;
  /** undefined: keep the saved key; "": remove it. */
  apiKey?: string;
  models: { id: string; label: string }[];
}

const draftOf = (provider: SpeechProvider): ProviderDraft => ({
  name: provider.name,
  baseUrl: provider.baseUrl,
  models: provider.models.map((model) => ({ id: model.id, label: model.label })),
});

const validUrl = (value: string) => {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
};

/** The parts of a provider its status and key fields need, shared by Transcription and Read aloud. */
type KeyedProvider = Pick<SpeechProvider, "name" | "keySet" | "keyHint" | "keyShared" | "keyOptional" | "status">;

export function StatusBadge({ provider }: { provider: Pick<KeyedProvider, "status"> }) {
  const { state, check } = provider.status;
  const label = state === "configured" && check?.ok ? "Connected" : STATE_LABELS[state];
  return <span className={`service-badge is-${state}`}><i aria-hidden="true" />{label}</span>;
}

export function KeyField({ provider, value, onChange, inputId }: { provider: KeyedProvider; value: string | undefined; onChange: (value: string | undefined) => void; inputId: string }) {
  const [visible, setVisible] = useState(false);
  const [replacing, setReplacing] = useState(false);
  useEffect(() => setReplacing(false), [provider.keyHint, provider.keySet]);
  if (provider.keySet && !replacing && !value) {
    const removing = value === "";
    return <div className="service-secret-saved">
      <span className={`service-secret-mask ${removing ? "is-removing" : ""}`}><KeyRound size={13} aria-hidden="true" />{removing ? "Removed when you save" : `•••• ${provider.keyHint ?? "saved"}`}</span>
      {removing
        ? <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChange(undefined)}>Undo</button>
        : <>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setReplacing(true)}>Replace</button>
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChange("")}>Remove</button>
        </>}
    </div>;
  }
  return <div className="service-secret-input">
    <input id={inputId} type={visible ? "text" : "password"} value={value ?? ""} placeholder={provider.keyShared ? `Using your ${provider.name} key from Models` : provider.keyOptional ? "Only if your server asks for one" : "Paste your key"} autoComplete="off" spellCheck={false} autoFocus={replacing} onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)} />
    <button type="button" className="gx-icon-button" aria-label={visible ? "Hide API key" : "Show API key"} title={visible ? "Hide" : "Show"} onClick={() => setVisible((current) => !current)}>
      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
    </button>
    {replacing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setReplacing(false); onChange(undefined); }}>Cancel</button>}
  </div>;
}

function ProviderEditor({ provider, onOverview, onRemoved, onNotify }: {
  provider: SpeechProvider;
  onOverview: (next: SpeechOverview) => void;
  onRemoved: () => void;
  onNotify: (message: string) => void;
}) {
  const baseId = useId();
  const initial = draftOf(provider);
  const [draft, setDraft] = useState<ProviderDraft>(initial);
  const [newModel, setNewModel] = useState("");
  const [available, setAvailable] = useState<string[] | null>(null);
  const [result, setResult] = useState<SpeechTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"saving" | "testing" | "removing" | null>(null);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const urlProblem = validUrl(draft.baseUrl) ? null : "Use a full address starting with http:// or https://.";

  const edit = (next: Partial<ProviderDraft>) => {
    setDraft((current) => ({ ...current, ...next }));
    setResult(null);
    setError(null);
  };
  const addModel = (id: string) => {
    const clean = id.trim();
    if (!clean || /\s/.test(clean) || draft.models.some((model) => model.id === clean)) return;
    edit({ models: [...draft.models, { id: clean, label: "" }] });
    setNewModel("");
  };

  const test = async () => {
    setBusy("testing");
    setError(null);
    try {
      const tested = await knowledgeApi.testSpeechProvider(provider.id, { baseUrl: draft.baseUrl, ...(draft.apiKey ? { apiKey: draft.apiKey } : {}) });
      setResult(tested);
      setAvailable(tested.ok ? tested.available : null);
      if (tested.overview) onOverview(tested.overview);
    } catch (reason) {
      setError(message(reason, "The connection could not be tested."));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (urlProblem || !changed) return;
    setBusy("saving");
    setError(null);
    try {
      const next = await knowledgeApi.updateSpeechProvider(provider.id, {
        name: draft.name,
        baseUrl: draft.baseUrl.trim(),
        ...(draft.apiKey !== undefined ? { apiKey: draft.apiKey } : {}),
        models: draft.models,
      });
      onOverview(next);
      setDraft(draftOf(next.providers.find((item) => item.id === provider.id) ?? provider));
      onNotify(`${draft.name} saved.`);
    } catch (reason) {
      setError(message(reason, `${provider.name} could not be saved.`));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Remove ${provider.name}? Jobs that use its models move to another model.`)) return;
    setBusy("removing");
    try {
      onOverview(await knowledgeApi.removeSpeechProvider(provider.id));
      onRemoved();
      onNotify(`${provider.name} removed.`);
    } catch (reason) {
      setError(message(reason, `${provider.name} could not be removed.`));
      setBusy(null);
    }
  };

  const offered = available?.filter((id) => !draft.models.some((model) => model.id === id)) ?? [];
  const outcome = error
    ? <p className="service-outcome is-error" role="alert"><CircleAlert size={14} />{error}</p>
    : result
      ? <p className={`service-outcome ${result.ok ? result.warning ? "is-warning" : "is-ok" : "is-error"}`} role={result.ok ? "status" : "alert"}>
        {result.ok ? result.warning ? <TriangleAlert size={14} /> : <Check size={14} /> : <CircleAlert size={14} />}
        <span>{result.message}{result.warning && <small>{result.warning}</small>}</span>
      </p>
      : busy === "testing" ? <p className="service-outcome" role="status"><span className="gx-spinner" aria-hidden="true" />Connecting…</p> : null;

  return <form className="service-editor" noValidate onSubmit={(event) => { event.preventDefault(); void save(); }}>
    {provider.note && <p className="service-editor-lead">{provider.note}</p>}
    <div className="service-fields">
      <div className="service-field">
        <label htmlFor={`${baseId}-name`}>Name</label>
        <div className="service-control"><input id={`${baseId}-name`} type="text" value={draft.name} maxLength={60} onChange={(event) => edit({ name: event.target.value })} /></div>
      </div>
      <div className={`service-field ${urlProblem ? "has-problem" : ""}`}>
        <label htmlFor={`${baseId}-url`}>Address</label>
        <div className="service-control">
          <input id={`${baseId}-url`} className="is-mono" type="url" value={draft.baseUrl} spellCheck={false} aria-invalid={Boolean(urlProblem) || undefined} onChange={(event) => edit({ baseUrl: event.target.value })} />
          <small className={urlProblem ? "is-problem" : ""}>{urlProblem ?? (provider.kind === "sensevoice" ? "Where the SenseVoice server runs." : "The base URL, without /audio/transcriptions.")}</small>
        </div>
      </div>
      {provider.kind !== "sensevoice" && <div className="service-field">
        <label htmlFor={`${baseId}-key`}>API key{provider.keySource === "environment" && draft.apiKey === undefined && <span className="service-source" title="Set in the backend's environment or .env file. A key saved here takes its place.">from .env</span>}</label>
        <div className="service-control">
          <KeyField provider={provider} value={draft.apiKey} inputId={`${baseId}-key`} onChange={(apiKey) => setDraft((current) => { const next = { ...current }; if (apiKey === undefined) delete next.apiKey; else next.apiKey = apiKey; return next; })} />
          {provider.keyShared && !provider.keySet && <small>No key needed here: the {provider.name} key you already saved is used.</small>}
        </div>
      </div>}
      <div className="service-field">
        <label>Models</label>
        <div className="service-control">
          <div className="provider-models">
            {draft.models.map((model) => <div className="provider-model" key={model.id}>
              <input className="provider-model-label" type="text" value={model.label} placeholder={model.id} aria-label={`Name for ${model.id}`} onChange={(event) => edit({ models: draft.models.map((item) => item.id === model.id ? { ...item, label: event.target.value } : item) })} />
              <code title={model.id}>{model.id}</code>
              <button type="button" className="gx-icon-button" aria-label={`Remove ${model.id}`} title="Remove" onClick={() => edit({ models: draft.models.filter((item) => item.id !== model.id) })}><X size={13} /></button>
            </div>)}
            {draft.models.length === 0 && <p className="provider-models-empty">No models yet. Fetch the ones it offers, or type a model's id.</p>}
          </div>
          <div className="provider-model-add">
            <input type="text" className="is-mono" value={newModel} placeholder="Model id, e.g. whisper-1" spellCheck={false} aria-label="Model id to add" onChange={(event) => setNewModel(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addModel(newModel); } }} />
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!newModel.trim()} onClick={() => addModel(newModel)}><Plus size={13} />Add</button>
            <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy !== null || Boolean(urlProblem)} onClick={() => void test()}><RefreshCw size={13} />Fetch models</button>
          </div>
          {available && <div className="provider-offered" aria-label="Models this provider offers">
            {offered.length === 0 ? <small>Every model it offers is already added.</small> : offered.slice(0, 60).map((id) => <button type="button" key={id} className="gx-chip" onClick={() => addModel(id)}><Plus size={12} /><span>{id}</span></button>)}
          </div>}
        </div>
      </div>
    </div>
    <footer className="service-editor-footer">
      <div className="service-outcome-slot" aria-live="polite">{outcome}</div>
      <div className="service-actions">
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm provider-remove" disabled={busy !== null} onClick={() => void remove()}><Trash2 size={13} />Remove</button>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy !== null || Boolean(urlProblem)} onClick={() => void test()}><Plug size={13} />{busy === "testing" ? "Testing…" : "Test connection"}</button>
        {changed && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy !== null} onClick={() => { setDraft(initial); setResult(null); setError(null); }}>Discard</button>}
        <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={!changed || busy !== null || Boolean(urlProblem)}>{busy === "saving" ? "Saving…" : "Save"}</button>
      </div>
    </footer>
  </form>;
}

type Choice = { model: string | null; stream: boolean; language: string };

function RoleRow({ role, overview, onSave }: { role: SpeechRole; overview: SpeechOverview; onSave: (role: SpeechRole["id"], choice: Choice) => void }) {
  const id = useId();
  const [language, setLanguage] = useState(role.language);
  useEffect(() => setLanguage(role.language), [role.language]);
  const choice = (next: Partial<Choice>): Choice => ({ model: role.model, stream: role.stream, language: role.language, ...next });
  const commitLanguage = () => { if (language.trim() !== role.language) onSave(role.id, choice({ language: language.trim() })); };
  return <div className={`model-role ${role.problem ? "has-problem" : ""}`}>
    <span className="model-role-name"><strong id={`${id}-label`}>{role.label}</strong><small>{role.description}</small></span>
    <div className="model-role-controls">
      <select aria-labelledby={`${id}-label`} value={role.model ?? ""} onChange={(event) => onSave(role.id, choice({ model: event.target.value || null }))}>
        {!role.model && <option value="">Choose a model</option>}
        {overview.providers.map((provider) => <optgroup key={provider.id} label={provider.name}>
          {provider.models.map((model) => <option key={model.ref} value={model.ref}>{model.label}{provider.keySet || provider.keyOptional ? "" : " (needs a key)"}</option>)}
        </optgroup>)}
      </select>
      <input className="speech-language" type="text" value={language} maxLength={12} placeholder="Language" aria-label={`${role.label} language`} title="A code like en or zh. Empty lets the model detect it." spellCheck={false} onChange={(event) => setLanguage(event.target.value)} onBlur={commitLanguage} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commitLanguage(); } }} />
    </div>
    {role.problem && <small className="model-role-problem"><CircleAlert size={12} />{role.problem}</small>}
  </div>;
}

/** Settings → Transcription: which model writes the words for recording and for Ask, and the providers they come from. */
export function SpeechSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const [overview, setOverview] = useState<SpeechOverview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let active = true;
    void knowledgeApi.speechOverview().then((next) => { if (active) setOverview(next); }).catch((reason) => {
      if (active) setProblem(message(reason, "Transcription could not be loaded."));
    });
    return () => { active = false; };
  }, []);

  const saveRole = async (role: SpeechRole["id"], choice: Choice) => {
    try {
      const next = await knowledgeApi.saveSpeechRoles({ [role]: choice });
      setOverview(next);
      const label = next.roles.find((item) => item.id === role)?.label ?? role;
      onNotify(`${label} saved.`);
    } catch (reason) {
      onNotify(message(reason, "The job could not be saved."));
    }
  };

  const add = async (kind: SpeechKind) => {
    try {
      const before = new Set(overview?.providers.map((provider) => provider.id));
      const next = await knowledgeApi.addSpeechProvider({ kind });
      setOverview(next);
      setAdding(false);
      setOpen(next.providers.find((provider) => !before.has(provider.id))?.id ?? null);
    } catch (reason) {
      onNotify(message(reason, "The provider could not be added."));
    }
  };

  return <section className="service-settings model-settings" aria-labelledby="speech-settings-heading">
    <div className="setting-heading">
      <AudioLines size={16} />
      <span><strong id="speech-settings-heading">Transcription</strong><small>Which model writes the words when you record and when you speak into Ask. Audio is always saved on this computer first.</small></span>
    </div>
    {!overview && !problem && <div className="setting-row"><span><strong>Loading…</strong><small>Asking the local service which transcription models are set up.</small></span></div>}
    {problem && <div className="setting-row is-problem"><span><strong><CircleAlert size={13} />Unavailable</strong><small>{problem}</small></span></div>}
    {overview && <>
      <div className="model-roles" role="group" aria-label="Used for">
        <div className="gx-menu-label">Used for</div>
        {overview.roles.map((role) => <RoleRow key={role.id} role={role} overview={overview} onSave={(id, choice) => void saveRole(id, choice)} />)}
      </div>
      <div className="gx-menu-label model-providers-label">Providers</div>
      <ul className="service-list">
        {overview.providers.map((provider) => {
          const expanded = open === provider.id;
          const panelId = `speech-panel-${provider.id}`;
          return <li key={provider.id} className={`service-item ${expanded ? "is-open" : ""}`}>
            <button type="button" className="service-summary" aria-expanded={expanded} aria-controls={panelId} onClick={() => setOpen(expanded ? null : provider.id)}>
              <span className="service-icon" aria-hidden="true">{provider.kind === "compatible" || provider.kind === "sensevoice" ? <Server size={15} /> : <span className="provider-initial">{provider.name.slice(0, 1)}</span>}</span>
              <span className="service-heading"><strong>{provider.name}</strong><small>{provider.status.summary}{provider.models.length > 0 && provider.status.state !== "error" ? ` · ${provider.models.map((model) => model.label).join(", ")}` : ""}</small></span>
              <StatusBadge provider={provider} />
              <ChevronRight size={15} className="service-chevron" aria-hidden="true" />
            </button>
            {expanded && <div id={panelId} className="service-panel">
              <ProviderEditor key={provider.id} provider={provider} onOverview={setOverview} onRemoved={() => setOpen(null)} onNotify={onNotify} />
            </div>}
          </li>;
        })}
        <li className="service-item provider-add">
          {!adding
            ? <button type="button" className="service-summary" onClick={() => setAdding(true)}><span className="service-icon" aria-hidden="true"><Plus size={15} /></span><span className="service-heading"><strong>Add provider</strong><small>SenseVoice, Qwen, OpenAI, or your own server</small></span></button>
            : <div className="provider-presets" role="group" aria-label="Choose a provider">
              {overview.presets.map((preset) => <button type="button" key={preset.kind} className="gx-chip" onClick={() => void add(preset.kind)}><span className="provider-initial" aria-hidden="true">{preset.name.slice(0, 1)}</span><span>{preset.name}</span></button>)}
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setAdding(false)}>Cancel</button>
            </div>}
        </li>
      </ul>
      {overview.fromEnvironment && import.meta.env.DEV && <p className="library-folder-note">These come from the backend’s .env (STT_*, SENSEVOICE_* or QWEN_STT_* settings). Anything you change here is saved in Gunther’s own settings and takes their place.</p>}
      {!overview.persisted && <p className="library-folder-note">This backend has no settings file, so changes last until it restarts.</p>}
    </>}
  </section>;
}
