import type { TtsOption, TtsOverview, TtsPreset, TtsProvider, TtsRole, TtsRoleInput, TtsSampleInput } from "@gunther/contracts";
import { ChevronRight, CircleAlert, Loader2, Play, Plus, Trash2, Volume2, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import { KeyField, StatusBadge } from "../models/SpeechSettings";
import { readAloud } from "./readAloud";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const megabytes = (bytes: number) => bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const validUrl = (value: string) => { try { return /^https?:$/.test(new URL(value.trim()).protocol); } catch { return false; } };

/** One sample at a time, across the page: a new one stops the last. */
let preview: HTMLAudioElement | null = null;
async function playSample(payload: TtsSampleInput) {
  readAloud.stop();
  const blob = await knowledgeApi.ttsSample(payload);
  preview?.pause();
  const url = URL.createObjectURL(blob);
  const player = new Audio(url);
  player.onended = () => URL.revokeObjectURL(url);
  preview = player;
  await player.play();
}

function OptionField({ option, value, onChange }: { option: TtsOption; value: string; onChange: (value: string) => void }) {
  const id = useId();
  const listed = option.choices.some((choice) => choice.value === value);
  const [custom, setCustom] = useState(!listed && option.choices.length > 0 ? true : option.choices.length === 0);
  const [typed, setTyped] = useState(value);
  useEffect(() => setTyped(value), [value]);
  const commit = () => { if (typed.trim() && typed.trim() !== value) onChange(typed.trim()); };
  return <span className="tts-option" title={option.help || undefined}>
    <label htmlFor={id}>{option.label}</label>
    {custom
      ? <input id={id} type="text" value={typed} maxLength={80} spellCheck={false} onChange={(event) => setTyped(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } }} />
      : <select id={id} value={value} onChange={(event) => { if (event.target.value === "\u0000custom") setCustom(true); else onChange(event.target.value); }}>
        {option.choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        {option.allowCustom && <option value={"\u0000custom"}>Other…</option>}
      </select>}
    {custom && option.choices.length > 0 && <button type="button" className="gx-icon-button" aria-label={`Choose ${option.label.toLowerCase()} from the list`} title="Use the list" onClick={() => { setCustom(false); onChange(option.default); }}><X size={12} /></button>}
  </span>;
}

function RoleRow({ role, overview, onSave, onNotify, onChecked }: { role: TtsRole; overview: TtsOverview; onSave: (role: TtsRole["id"], change: TtsRoleInput) => void; onNotify: (message: string) => void; onChecked: () => void }) {
  const id = useId();
  const [hearing, setHearing] = useState(false);
  const preset: TtsPreset | undefined = overview.presets.find((item) => item.kind === role.kind);
  const hear = async () => {
    setHearing(true);
    try {
      await playSample({ options: role.options });
    } catch (reason) {
      onNotify(message(reason, "The sample could not be played."));
    } finally {
      setHearing(false);
      onChecked();
    }
  };
  return <div className={`model-role tts-role ${role.problem ? "has-problem" : ""}`}>
    <span className="model-role-name"><strong id={`${id}-label`}>{role.label}</strong><small>{role.description}</small></span>
    <div className="model-role-controls">
      <select aria-labelledby={`${id}-label`} value={role.model ?? ""} onChange={(event) => onSave(role.id, { model: event.target.value || null })}>
        <option value="">Off</option>
        {overview.providers.map((provider) => <optgroup key={provider.id} label={provider.name}>
          {provider.models.map((model) => <option key={model.ref} value={model.ref}>{model.label}{provider.keySet || provider.keyShared || provider.keyOptional ? "" : " (needs a key)"}</option>)}
        </optgroup>)}
      </select>
      {role.model && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={hearing || Boolean(role.problem)} onClick={() => void hear()}>{hearing ? <Loader2 size={13} className="is-spinning" /> : <Play size={13} />}Hear</button>}
    </div>
    {role.model && preset && <div className="tts-role-options">
      {preset.options.map((option) => <OptionField key={`${role.kind}-${option.key}`} option={option} value={role.options[option.key] ?? option.default} onChange={(value) => onSave(role.id, { options: { ...role.options, [option.key]: value } })} />)}
      <label className="speech-live" title="When an answer is finished it is spoken, like a conversation. You can pause it from the answer.">
        <input type="checkbox" role="switch" checked={role.autoRead} onChange={(event) => onSave(role.id, { autoRead: event.target.checked })} />
        <span>Read new answers automatically</span>
      </label>
    </div>}
    {role.problem && <small className="model-role-problem"><CircleAlert size={12} />{role.problem}</small>}
  </div>;
}

interface ProviderDraft {
  name: string;
  baseUrl: string;
  /** undefined: keep the saved key; "": remove it. */
  apiKey?: string;
  models: { id: string; label: string }[];
}

const draftOf = (provider: TtsProvider): ProviderDraft => ({
  name: provider.name,
  baseUrl: provider.baseUrl,
  models: provider.models.map((model) => ({ id: model.id, label: model.label })),
});

function ProviderEditor({ provider, onOverview, onRemoved, onNotify, onChecked }: {
  provider: TtsProvider;
  onOverview: (next: TtsOverview) => void;
  onRemoved: () => void;
  onNotify: (message: string) => void;
  /** A sample was tried: the service remembers the outcome, so the badge can say Connected. */
  onChecked: () => void;
}) {
  const baseId = useId();
  const initial = draftOf(provider);
  const [draft, setDraft] = useState<ProviderDraft>(initial);
  const [newModel, setNewModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"saving" | "sample" | "removing" | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const urlProblem = validUrl(draft.baseUrl) ? null : "Use a full address starting with http:// or https://.";

  const edit = (next: Partial<ProviderDraft>) => {
    setDraft((current) => ({ ...current, ...next }));
    setError(null);
  };
  const addModel = (id: string) => {
    const clean = id.trim();
    if (!clean || /\s/.test(clean) || draft.models.some((model) => model.id === clean)) return;
    edit({ models: [...draft.models, { id: clean, label: "" }] });
    setNewModel("");
  };

  const hear = async () => {
    setBusy("sample");
    setError(null);
    try {
      await playSample({ providerId: provider.id, baseUrl: draft.baseUrl.trim(), ...(draft.apiKey ? { apiKey: draft.apiKey } : {}), ...(draft.models[0] ? { model: draft.models[0].id } : {}) });
    } catch (reason) {
      if (mounted.current) setError(message(reason, "The sample could not be played."));
    } finally {
      if (mounted.current) setBusy(null);
      onChecked();
    }
  };

  const save = async () => {
    if (urlProblem || !changed) return;
    setBusy("saving");
    setError(null);
    try {
      const next = await knowledgeApi.updateTtsProvider(provider.id, {
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
    if (!window.confirm(`Remove ${provider.name}? A job that uses its voice moves to another one, or turns off.`)) return;
    setBusy("removing");
    try {
      onOverview(await knowledgeApi.removeTtsProvider(provider.id));
      onRemoved();
      onNotify(`${provider.name} removed.`);
    } catch (reason) {
      setError(message(reason, `${provider.name} could not be removed.`));
      setBusy(null);
    }
  };

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
          <small className={urlProblem ? "is-problem" : ""}>{urlProblem ?? "The supplier's address. Keys work only in the region they were made in."}</small>
        </div>
      </div>
      <div className="service-field">
        <label htmlFor={`${baseId}-key`}>API key</label>
        <div className="service-control">
          <KeyField provider={provider} value={draft.apiKey} inputId={`${baseId}-key`} onChange={(apiKey) => setDraft((current) => { const next = { ...current }; if (apiKey === undefined) delete next.apiKey; else next.apiKey = apiKey; return next; })} />
          {provider.keyShared && !provider.keySet && <small>No key needed here: the {provider.name} key you already saved is used.</small>}
        </div>
      </div>
      <div className="service-field">
        <label>Models</label>
        <div className="service-control">
          <div className="provider-models">
            {draft.models.map((model) => <div className="provider-model" key={model.id}>
              <input className="provider-model-label" type="text" value={model.label} placeholder={model.id} aria-label={`Name for ${model.id}`} onChange={(event) => edit({ models: draft.models.map((item) => item.id === model.id ? { ...item, label: event.target.value } : item) })} />
              <code title={model.id}>{model.id}</code>
              <button type="button" className="gx-icon-button" aria-label={`Remove ${model.id}`} title="Remove" onClick={() => edit({ models: draft.models.filter((item) => item.id !== model.id) })}><X size={13} /></button>
            </div>)}
            {draft.models.length === 0 && <p className="provider-models-empty">No models yet. Type the id of a model your supplier offers.</p>}
          </div>
          <div className="provider-model-add">
            <input type="text" className="is-mono" value={newModel} placeholder="Model id, e.g. qwen-audio-3.1-tts-flash" spellCheck={false} aria-label="Model id to add" onChange={(event) => setNewModel(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addModel(newModel); } }} />
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!newModel.trim()} onClick={() => addModel(newModel)}><Plus size={13} />Add</button>
          </div>
        </div>
      </div>
    </div>
    <footer className="service-editor-footer">
      <div className="service-outcome-slot" aria-live="polite">
        {error ? <p className="service-outcome is-error" role="alert"><CircleAlert size={14} />{error}</p> : busy === "sample" ? <p className="service-outcome" role="status"><span className="gx-spinner" aria-hidden="true" />Making a sample…</p> : null}
      </div>
      <div className="service-actions">
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm provider-remove" disabled={busy !== null} onClick={() => void remove()}><Trash2 size={13} />Remove</button>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy !== null || Boolean(urlProblem) || draft.models.length === 0} onClick={() => void hear()}><Play size={13} />Hear a sample</button>
        {changed && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy !== null} onClick={() => { setDraft(initial); setError(null); }}>Discard</button>}
        <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={!changed || busy !== null || Boolean(urlProblem)}>{busy === "saving" ? "Saving…" : "Save"}</button>
      </div>
    </footer>
  </form>;
}

/** Settings → Read aloud: which voice speaks answers, and the providers voices come from. Laid out like Transcription. */
export function TtsSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const [overview, setOverview] = useState<TtsOverview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [clearing, setClearing] = useState(false);
  /** After a sample: the service kept its outcome, so the badges (Connected, or the error) follow. */
  const refresh = () => { void knowledgeApi.ttsOverview().then(setOverview).catch(() => undefined); };

  useEffect(() => {
    let active = true;
    void knowledgeApi.ttsOverview().then((next) => { if (active) setOverview(next); }).catch((reason) => {
      if (active) setProblem(message(reason, "Reading aloud could not be loaded."));
    });
    return () => { active = false; preview?.pause(); };
  }, []);

  const saveRole = async (role: TtsRole["id"], change: TtsRoleInput) => {
    try {
      const next = await knowledgeApi.saveTtsRoles({ [role]: change });
      setOverview(next);
      onNotify(`${next.roles.find((item) => item.id === role)?.label ?? "Voice"} saved.`);
    } catch (reason) {
      onNotify(message(reason, "That could not be saved."));
    }
  };

  const add = async (kind: string) => {
    try {
      const before = new Set(overview?.providers.map((provider) => provider.id));
      const next = await knowledgeApi.addTtsProvider({ kind });
      setOverview(next);
      setAdding(false);
      setOpen(next.providers.find((provider) => !before.has(provider.id))?.id ?? null);
    } catch (reason) {
      onNotify(message(reason, "The provider could not be added."));
    }
  };

  const clear = async () => {
    setClearing(true);
    try {
      setOverview(await knowledgeApi.clearTtsCache());
      onNotify("Saved recordings deleted. Answers are read again when you ask.");
    } catch (reason) {
      onNotify(message(reason, "The recordings could not be deleted."));
    } finally {
      setClearing(false);
    }
  };

  const answers = overview?.roles.find((role) => role.id === "answers");
  const speaking = overview?.presets.find((preset) => preset.kind === answers?.kind);
  return <section className="service-settings model-settings tts-settings" aria-labelledby="tts-settings-heading">
    <div className="setting-heading">
      <Volume2 size={16} />
      <span><strong id="tts-settings-heading">Read aloud</strong><small>Have answers spoken to you. The first time an answer is read it is made and kept on this computer; after that it only plays.</small></span>
    </div>
    {!overview && !problem && <div className="setting-row"><span><strong>Loading…</strong><small>Asking the local service which voices are set up.</small></span></div>}
    {problem && <div className="setting-row is-problem"><span><strong><CircleAlert size={13} />Unavailable</strong><small>{problem}</small></span></div>}
    {overview && <>
      <div className="model-roles" role="group" aria-label="Used for">
        <div className="gx-menu-label">Used for</div>
        {overview.roles.map((role) => <RoleRow key={role.id} role={role} overview={overview} onSave={(id, change) => void saveRole(id, change)} onNotify={onNotify} onChecked={refresh} />)}
        {answers?.model && speaking && <p className="model-roles-note">{speaking.readsStructure
          ? `${speaking.name}'s voice model reads tables and pictures itself.`
          : "Tables, pictures, code and formulas are described in words by your language model before they are spoken, instead of being read cell by cell."}</p>}
      </div>
      <div className="gx-menu-label model-providers-label">Providers</div>
      <ul className="service-list">
        {overview.providers.map((provider) => {
          const expanded = open === provider.id;
          const panelId = `tts-panel-${provider.id}`;
          return <li key={provider.id} className={`service-item ${expanded ? "is-open" : ""}`}>
            <button type="button" className="service-summary" aria-expanded={expanded} aria-controls={panelId} onClick={() => setOpen(expanded ? null : provider.id)}>
              <span className="service-icon" aria-hidden="true"><span className="provider-initial">{provider.name.slice(0, 1)}</span></span>
              <span className="service-heading"><strong>{provider.name}</strong><small>{provider.status.summary}{provider.models.length > 0 && provider.status.state === "configured" ? ` · ${provider.models.map((model) => model.label).join(", ")}` : ""}</small></span>
              <StatusBadge provider={provider} />
              <ChevronRight size={15} className="service-chevron" aria-hidden="true" />
            </button>
            {expanded && <div id={panelId} className="service-panel">
              <ProviderEditor key={provider.id} provider={provider} onOverview={setOverview} onRemoved={() => setOpen(null)} onNotify={onNotify} onChecked={refresh} />
            </div>}
          </li>;
        })}
        <li className="service-item provider-add">
          {!adding
            ? <button type="button" className="service-summary" onClick={() => setAdding(true)}><span className="service-icon" aria-hidden="true"><Plus size={15} /></span><span className="service-heading"><strong>Add provider</strong><small>{overview.presets.map((preset) => preset.name).join(", ")}; more voices later</small></span></button>
            : <div className="provider-presets" role="group" aria-label="Choose a provider">
              {overview.presets.map((preset) => <button type="button" key={preset.kind} className="gx-chip" onClick={() => void add(preset.kind)}><span className="provider-initial" aria-hidden="true">{preset.name.slice(0, 1)}</span><span>{preset.name}</span></button>)}
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setAdding(false)}>Cancel</button>
            </div>}
        </li>
      </ul>
      <div className="setting-row">
        <span><strong>Saved recordings</strong><small>{overview.cache.clips === 0 ? "None yet." : `${overview.cache.clips} ${overview.cache.clips === 1 ? "answer" : "answers"} · ${megabytes(overview.cache.bytes)}. Deleting them only means answers are made again the next time.`}</small></span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={clearing || overview.cache.clips === 0} onClick={() => void clear()}>Delete</button>
      </div>
      {!overview.persisted && <p className="library-folder-note">This backend has no settings file, so changes last until it restarts.</p>}
    </>}
  </section>;
}
