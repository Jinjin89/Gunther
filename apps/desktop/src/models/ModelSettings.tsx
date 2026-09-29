import type { Effort, ModelProvider, ModelRole, ModelsOverview, ProviderKind, ProviderTestResult } from "@gunther/contracts";
import { EFFORTS } from "@gunther/contracts";
import { Check, ChevronRight, CircleAlert, Eye, EyeOff, KeyRound, Plug, Plus, RefreshCw, Server, Sparkles, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { knowledgeApi } from "../api";
import { announceModelsChanged, EFFORT_NAMES, resolveEffort } from "./askModel";

const STATE_LABELS = { configured: "Set up", not_configured: "Not set up", error: "Needs attention", off: "Off" } as const;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

interface ModelDraft {
  id: string;
  label: string;
  /** null: what Gunther knows about this model. */
  vision: boolean | null;
  dialect: string | null;
}

interface ProviderDraft {
  name: string;
  baseUrl: string;
  /** undefined: keep the saved key; "": remove it. */
  apiKey?: string;
  models: ModelDraft[];
}

const draftOf = (provider: ModelProvider): ProviderDraft => ({
  name: provider.name,
  baseUrl: provider.baseUrl,
  models: provider.models.map((model) => ({
    id: model.id,
    label: model.label,
    vision: model.vision === model.visionBuiltIn ? null : model.vision,
    dialect: model.dialect === model.dialectBuiltIn ? null : model.dialect,
  })),
});

const validUrl = (value: string) => {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
};

function StatusBadge({ provider }: { provider: ModelProvider }) {
  const { state, check } = provider.status;
  const label = state === "configured" && check?.ok ? "Connected" : STATE_LABELS[state];
  return <span className={`service-badge is-${state}`}><i aria-hidden="true" />{label}</span>;
}

function KeyField({ provider, value, onChange, inputId }: { provider: ModelProvider; value: string | undefined; onChange: (value: string | undefined) => void; inputId: string }) {
  const [visible, setVisible] = useState(false);
  const [replacing, setReplacing] = useState(false);
  useEffect(() => setReplacing(false), [provider.keyHint, provider.keySet]);
  const saved = provider.keySource === "saved" || provider.keySource === "environment";
  if (provider.keySet && saved && !replacing && !value) {
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
    <input
      id={inputId}
      type={visible ? "text" : "password"}
      value={value ?? ""}
      placeholder={provider.keyOptional ? "Only if your server asks for one" : "Paste your key"}
      autoComplete="off"
      spellCheck={false}
      autoFocus={replacing}
      onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
    />
    <button type="button" className="gx-icon-button" aria-label={visible ? "Hide API key" : "Show API key"} title={visible ? "Hide" : "Show"} onClick={() => setVisible((current) => !current)}>
      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
    </button>
    {replacing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setReplacing(false); onChange(undefined); }}>Cancel</button>}
  </div>;
}

function ProviderEditor({ provider, overview, onOverview, onRemoved, onNotify }: {
  provider: ModelProvider;
  overview: ModelsOverview;
  onOverview: (next: ModelsOverview) => void;
  onRemoved: () => void;
  onNotify: (message: string) => void;
}) {
  const baseId = useId();
  const initial = draftOf(provider);
  const [draft, setDraft] = useState<ProviderDraft>(initial);
  const [newModel, setNewModel] = useState("");
  const [available, setAvailable] = useState<string[] | null>(null);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"saving" | "testing" | "removing" | null>(null);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const urlProblem = validUrl(draft.baseUrl) ? null : "Use a full address starting with http:// or https://.";
  const known = new Map(provider.models.map((model) => [model.id, model]));

  const edit = (next: Partial<ProviderDraft>) => {
    setDraft((current) => ({ ...current, ...next }));
    setResult(null);
    setError(null);
  };
  const editModel = (id: string, next: Partial<ModelDraft>) => edit({ models: draft.models.map((model) => model.id === id ? { ...model, ...next } : model) });
  const addModel = (id: string) => {
    const clean = id.trim();
    if (!clean || /\s/.test(clean) || draft.models.some((model) => model.id === clean)) return;
    edit({ models: [...draft.models, { id: clean, label: "", vision: null, dialect: null }] });
    setNewModel("");
  };

  const test = async () => {
    setBusy("testing");
    setError(null);
    try {
      const tested = await knowledgeApi.testProvider(provider.id, { baseUrl: draft.baseUrl, ...(draft.apiKey ? { apiKey: draft.apiKey } : {}) });
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
      const next = await knowledgeApi.updateProvider(provider.id, {
        name: draft.name,
        baseUrl: draft.baseUrl.trim(),
        ...(draft.apiKey !== undefined ? { apiKey: draft.apiKey } : {}),
        models: draft.models.map((model) => ({ id: model.id, label: model.label, vision: model.vision, dialect: model.dialect })),
      });
      onOverview(next);
      announceModelsChanged();
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
      onOverview(await knowledgeApi.removeProvider(provider.id));
      announceModelsChanged();
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
        <span>{result.message}{result.ok && ` ${result.available.length} models offered.`}{result.warning && <small>{result.warning}</small>}</span>
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
        <label htmlFor={`${baseId}-url`}>Base URL</label>
        <div className="service-control">
          <input id={`${baseId}-url`} className="is-mono" type="url" value={draft.baseUrl} spellCheck={false} aria-invalid={Boolean(urlProblem) || undefined} onChange={(event) => edit({ baseUrl: event.target.value })} />
          <small className={urlProblem ? "is-problem" : ""}>{urlProblem ?? "Where its OpenAI-compatible API lives."}</small>
        </div>
      </div>
      <div className="service-field">
        <label htmlFor={`${baseId}-key`}>API key{provider.keySource === "environment" && draft.apiKey === undefined && <span className="service-source" title="Set in the backend's environment or .env file. A key saved here takes its place.">from .env</span>}</label>
        <div className="service-control"><KeyField provider={provider} value={draft.apiKey} inputId={`${baseId}-key`} onChange={(apiKey) => setDraft((current) => { const next = { ...current }; if (apiKey === undefined) delete next.apiKey; else next.apiKey = apiKey; return next; })} /></div>
      </div>
      <div className="service-field">
        <label>Models</label>
        <div className="service-control">
          <div className="provider-models">
            {draft.models.map((model) => {
              const saved = known.get(model.id);
              const vision = model.vision ?? saved?.visionBuiltIn ?? false;
              return <div className="provider-model" key={model.id}>
                <input className="provider-model-label" type="text" value={model.label} placeholder={model.id} aria-label={`Name for ${model.id}`} onChange={(event) => editModel(model.id, { label: event.target.value })} />
                <code title={model.id}>{model.id}</code>
                <label className="provider-model-vision" title="Whether it can look at photos">
                  <input type="checkbox" checked={vision} onChange={(event) => editModel(model.id, { vision: event.target.checked === (saved?.visionBuiltIn ?? false) ? null : event.target.checked })} />
                  <span>Sees images</span>
                </label>
                <select aria-label={`Thinking setting for ${model.id}`} value={model.dialect ?? ""} onChange={(event) => editModel(model.id, { dialect: event.target.value || null })}>
                  <option value="">{saved ? `Built in: ${overview.dialects.find((dialect) => dialect.id === saved.dialectBuiltIn)?.label ?? "none"}` : "Automatic"}</option>
                  {overview.dialects.map((dialect) => <option key={dialect.id} value={dialect.id}>{dialect.label}</option>)}
                </select>
                <button type="button" className="gx-icon-button" aria-label={`Remove ${model.id}`} title="Remove" onClick={() => edit({ models: draft.models.filter((item) => item.id !== model.id) })}><X size={13} /></button>
              </div>;
            })}
            {draft.models.length === 0 && <p className="provider-models-empty">No models yet. Fetch the ones it offers, or type a model's id.</p>}
          </div>
          <div className="provider-model-add">
            <input type="text" className="is-mono" value={newModel} placeholder="Model id, e.g. glm-5.3-flash" spellCheck={false} aria-label="Model id to add" onChange={(event) => setNewModel(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addModel(newModel); } }} />
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!newModel.trim()} onClick={() => addModel(newModel)}><Plus size={13} />Add</button>
            <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy !== null || Boolean(urlProblem)} onClick={() => void test()}><RefreshCw size={13} />Fetch models</button>
          </div>
          {available && <div className="provider-offered" aria-label="Models this provider offers">
            {offered.length === 0 ? <small>Every model it offers is already added.</small> : offered.slice(0, 60).map((id) => <button type="button" key={id} className="gx-chip" onClick={() => addModel(id)}><Plus size={12} /><span>{id}</span></button>)}
            {offered.length > 60 && <small>and {offered.length - 60} more; type an id to add it.</small>}
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

function RoleRow({ role, overview, onSave }: { role: ModelRole; overview: ModelsOverview; onSave: (role: ModelRole["id"], choice: { model: string | null; effort: Effort }) => void }) {
  const id = useId();
  const current = overview.providers.flatMap((provider) => provider.models).find((model) => model.ref === role.model);
  return <div className={`model-role ${role.problem ? "has-problem" : ""}`}>
    <span className="model-role-name"><strong id={`${id}-label`}>{role.label}</strong><small>{role.description}</small></span>
    <div className="model-role-controls">
      <select aria-labelledby={`${id}-label`} value={role.model ?? ""} onChange={(event) => onSave(role.id, { model: event.target.value || null, effort: role.effort })}>
        {!role.model && <option value="">Choose a model</option>}
        {overview.providers.map((provider) => <optgroup key={provider.id} label={provider.name}>
          {provider.models.map((model) => <option key={model.ref} value={model.ref}>{model.label}{role.needsVision && !model.vision ? " (text only)" : ""}{provider.keySet || provider.keyOptional ? "" : " (needs a key)"}</option>)}
        </optgroup>)}
      </select>
      <select aria-label={`${role.label} thinking effort`} value={role.effort} disabled={!current || !current.levels.length} onChange={(event) => onSave(role.id, { model: role.model, effort: event.target.value as Effort })}>
        {EFFORTS.map((effort) => {
          const used = current ? resolveEffort(effort, current.levels) : null;
          return <option key={effort} value={effort}>{EFFORT_NAMES[effort]}{used && used.id !== effort ? ` (uses ${used.label})` : ""}</option>;
        })}
      </select>
    </div>
    {role.problem && <small className="model-role-problem"><CircleAlert size={12} />{role.problem}</small>}
  </div>;
}

/** Settings → Models: which model does each job, and the providers they come from. */
export function ModelSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let active = true;
    void knowledgeApi.modelsOverview().then((next) => { if (active) setOverview(next); }).catch((reason) => {
      if (active) setProblem(message(reason, "Models could not be loaded."));
    });
    return () => { active = false; };
  }, []);

  const saveRole = async (role: ModelRole["id"], choice: { model: string | null; effort: Effort }) => {
    try {
      const next = await knowledgeApi.saveModelRoles({ [role]: choice });
      setOverview(next);
      announceModelsChanged();
      const chosen = next.providers.flatMap((provider) => provider.models.map((model) => ({ ...model, provider: provider.name }))).find((model) => model.ref === choice.model);
      const label = next.roles.find((item) => item.id === role)?.label ?? role;
      onNotify(chosen ? `${label} uses ${chosen.provider} · ${chosen.label}, ${EFFORT_NAMES[choice.effort]} effort.` : `${label} saved.`);
    } catch (reason) {
      onNotify(message(reason, "The job could not be saved."));
    }
  };

  const add = async (kind: ProviderKind) => {
    try {
      const before = new Set(overview?.providers.map((provider) => provider.id));
      const next = await knowledgeApi.addProvider({ kind });
      setOverview(next);
      announceModelsChanged();
      setAdding(false);
      setOpen(next.providers.find((provider) => !before.has(provider.id))?.id ?? null);
    } catch (reason) {
      onNotify(message(reason, "The provider could not be added."));
    }
  };

  return <section className="service-settings model-settings" aria-labelledby="model-settings-heading">
    <div className="setting-heading">
      <Sparkles size={16} />
      <span><strong id="model-settings-heading">Models</strong><small>The language models Gunther uses, and which one does each job. Keys stay on this computer.</small></span>
    </div>
    {!overview && !problem && <div className="setting-row"><span><strong>Loading…</strong><small>Asking the local service which models are set up.</small></span></div>}
    {problem && <div className="setting-row is-problem"><span><strong><CircleAlert size={13} />Unavailable</strong><small>{problem}</small></span></div>}
    {overview && <>
      <div className="model-roles" role="group" aria-label="Used for">
        <div className="gx-menu-label">Used for</div>
        {overview.roles.map((role) => <RoleRow key={role.id} role={role} overview={overview} onSave={(id, choice) => void saveRole(id, choice)} />)}
        <p className="model-roles-note">Ask can switch model and effort in any conversation; these are where new ones start.</p>
      </div>
      <div className="gx-menu-label model-providers-label">Providers</div>
      <ul className="service-list">
        {overview.providers.map((provider) => {
          const expanded = open === provider.id;
          const panelId = `provider-panel-${provider.id}`;
          return <li key={provider.id} className={`service-item ${expanded ? "is-open" : ""}`}>
            <button type="button" className="service-summary" aria-expanded={expanded} aria-controls={panelId} onClick={() => setOpen(expanded ? null : provider.id)}>
              <span className="service-icon" aria-hidden="true">{provider.kind === "compatible" ? <Server size={15} /> : <span className="provider-initial">{provider.name.slice(0, 1)}</span>}</span>
              <span className="service-heading"><strong>{provider.name}</strong><small>{provider.status.summary}{provider.models.length > 0 && provider.status.state !== "error" ? ` · ${provider.models.map((model) => model.label).join(", ")}` : ""}</small></span>
              <StatusBadge provider={provider} />
              <ChevronRight size={15} className="service-chevron" aria-hidden="true" />
            </button>
            {expanded && <div id={panelId} className="service-panel">
              <ProviderEditor key={provider.id} provider={provider} overview={overview} onOverview={setOverview} onRemoved={() => setOpen(null)} onNotify={onNotify} />
            </div>}
          </li>;
        })}
        <li className="service-item provider-add">
          {!adding
            ? <button type="button" className="service-summary" onClick={() => setAdding(true)}><span className="service-icon" aria-hidden="true"><Plus size={15} /></span><span className="service-heading"><strong>Add provider</strong><small>DeepSeek, Kimi, GLM, Qwen, OpenAI, or your own server</small></span></button>
            : <div className="provider-presets" role="group" aria-label="Choose a provider">
              {overview.presets.map((preset) => <button type="button" key={preset.kind} className="gx-chip" onClick={() => void add(preset.kind)}>{preset.kind === "compatible" ? <Server size={14} aria-hidden="true" /> : <span className="provider-initial" aria-hidden="true">{preset.name.slice(0, 1)}</span>}<span>{preset.name}</span></button>)}
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setAdding(false)}>Cancel</button>
            </div>}
        </li>
      </ul>
      {overview.fromEnvironment && <p className="library-folder-note">These come from the backend’s .env (LLM_* or DEEPSEEK_* settings). Anything you change here is saved in Gunther’s own settings and takes their place.</p>}
      {!overview.persisted && <p className="library-folder-note">This backend has no settings file, so changes last until it restarts.</p>}
    </>}
  </section>;
}
