import type { TtsOverview, TtsProvider } from "@gunther/contracts";
import { CircleAlert, Eye, EyeOff, KeyRound, Loader2, Play, Volume2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import { readAloud } from "./readAloud";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const megabytes = (bytes: number) => bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const validUrl = (value: string) => { try { return /^https?:$/.test(new URL(value.trim()).protocol); } catch { return false; } };

interface Draft {
  baseUrl: string;
  model: string;
  /** undefined: keep the saved key; "": remove it. */
  apiKey?: string | undefined;
  options: Record<string, string>;
}

const draftOf = (provider: TtsProvider): Draft => ({ baseUrl: provider.baseUrl, model: provider.model, options: { ...provider.values } });

function OptionField({ option, value, onChange }: { option: TtsProvider["options"][number]; value: string; onChange: (value: string) => void }) {
  const id = useId();
  const listed = option.choices.some((choice) => choice.value === value);
  const [custom, setCustom] = useState(!listed);
  return <div className="tts-field">
    <label htmlFor={id}>{option.label}</label>
    {custom
      ? <input id={id} type="text" value={value} maxLength={80} spellCheck={false} onChange={(event) => onChange(event.target.value)} />
      : <select id={id} value={value} onChange={(event) => { if (event.target.value === "\u0000custom") { setCustom(true); } else onChange(event.target.value); }}>
        {option.choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        {option.allowCustom && <option value={"\u0000custom"}>Other…</option>}
      </select>}
    {custom && option.choices.length > 0 && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setCustom(false); onChange(option.default); }}>Use list</button>}
    {option.help && <small>{option.help}</small>}
  </div>;
}

function ProviderEditor({ provider, onOverview, onNotify }: { provider: TtsProvider; onOverview: (next: TtsOverview) => void; onNotify: (message: string) => void }) {
  const id = useId();
  const initial = draftOf(provider);
  const [draft, setDraft] = useState<Draft>(initial);
  const [visible, setVisible] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState<"saving" | "sample" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const preview = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => preview.current?.pause(), []);
  const changed = draft.apiKey !== undefined || JSON.stringify({ ...draft, apiKey: undefined }) !== JSON.stringify(initial);
  const urlProblem = validUrl(draft.baseUrl) ? null : "Use a full address starting with http:// or https://.";
  const edit = (next: Partial<Draft>) => { setDraft((current) => ({ ...current, ...next })); setError(null); };
  const body = () => ({
    kind: provider.kind,
    provider: { baseUrl: draft.baseUrl.trim(), model: draft.model.trim(), options: draft.options, ...(draft.apiKey !== undefined ? { apiKey: draft.apiKey } : {}) },
  });

  const hear = async () => {
    setBusy("sample");
    setError(null);
    readAloud.stop();
    try {
      const blob = await knowledgeApi.ttsSample(body());
      preview.current?.pause();
      const url = URL.createObjectURL(blob);
      const player = new Audio(url);
      player.onended = () => URL.revokeObjectURL(url);
      preview.current = player;
      await player.play();
    } catch (reason) {
      setError(message(reason, "The sample could not be played."));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (urlProblem || !changed) return;
    setBusy("saving");
    setError(null);
    try {
      const next = await knowledgeApi.saveTts(body());
      onOverview(next);
      setDraft(draftOf(next.providers.find((item) => item.kind === provider.kind) ?? provider));
      setReplacing(false);
      onNotify(`${provider.name} voice saved.`);
    } catch (reason) {
      setError(message(reason, "The voice could not be saved."));
    } finally {
      setBusy(null);
    }
  };

  const keySaved = provider.keySet && !replacing && draft.apiKey === undefined;
  return <form className="tts-editor" onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <div className="tts-field">
      <label htmlFor={`${id}-key`}>API key</label>
      {keySaved
        ? <div className="service-secret-saved">
          <span className="service-secret-mask"><KeyRound size={13} aria-hidden="true" />•••• {provider.keyHint ?? "saved"}</span>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setReplacing(true)}>Replace</button>
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => edit({ apiKey: "" })}>Remove</button>
        </div>
        : <div className="service-secret-input">
          <input id={`${id}-key`} type={visible ? "text" : "password"} value={draft.apiKey ?? ""} autoComplete="off" spellCheck={false} autoFocus={replacing}
            placeholder={provider.keyShared ? `Using your ${provider.name} key from Models` : provider.keyOptional ? "Only if your server asks for one" : "Paste your key"}
            onChange={(event) => edit({ apiKey: event.target.value === "" && !provider.keySet ? undefined : event.target.value })} />
          <button type="button" className="gx-icon-button" aria-label={visible ? "Hide API key" : "Show API key"} onClick={() => setVisible((current) => !current)}>{visible ? <EyeOff size={14} /> : <Eye size={14} />}</button>
          {(replacing || draft.apiKey === "") && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setReplacing(false); edit({ apiKey: undefined }); }}>Cancel</button>}
        </div>}
      {provider.keyShared && !provider.keySet && <small>No key needed here: the {provider.name} key you already saved is used.</small>}
    </div>
    <div className="tts-field">
      <label htmlFor={`${id}-model`}>Model</label>
      <input id={`${id}-model`} list={`${id}-models`} value={draft.model} spellCheck={false} onChange={(event) => edit({ model: event.target.value })} />
      <datalist id={`${id}-models`}>{provider.models.map((model) => <option key={model} value={model} />)}</datalist>
    </div>
    {provider.options.map((option) => <OptionField key={option.key} option={option} value={draft.options[option.key] ?? option.default} onChange={(value) => edit({ options: { ...draft.options, [option.key]: value } })} />)}
    <details className="tts-advanced">
      <summary>Address</summary>
      <div className="tts-field">
        <input aria-label="Address" value={draft.baseUrl} spellCheck={false} onChange={(event) => edit({ baseUrl: event.target.value })} />
        {urlProblem ? <small className="model-role-problem"><CircleAlert size={12} />{urlProblem}</small> : provider.note && <small>{provider.note}</small>}
      </div>
    </details>
    {error && <p className="model-role-problem" role="alert"><CircleAlert size={12} />{error}</p>}
    <div className="tts-actions">
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy !== null || Boolean(urlProblem)} onClick={() => void hear()}>{busy === "sample" ? <Loader2 size={13} className="is-spinning" /> : <Play size={13} />}Hear a sample</button>
      <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={!changed || busy !== null || Boolean(urlProblem)}>{busy === "saving" ? "Saving…" : "Save"}</button>
    </div>
  </form>;
}

/** Settings → Read aloud: which supplier speaks answers, its voice, and whether it reads by itself. */
export function TtsSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const [overview, setOverview] = useState<TtsOverview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    let active = true;
    void knowledgeApi.ttsOverview().then((next) => { if (active) setOverview(next); }).catch((reason) => {
      if (active) setProblem(message(reason, "Reading aloud could not be loaded."));
    });
    return () => { active = false; };
  }, []);

  const change = async (payload: Parameters<typeof knowledgeApi.saveTts>[0], done?: string) => {
    try {
      setOverview(await knowledgeApi.saveTts(payload));
      if (done) onNotify(done);
    } catch (reason) {
      onNotify(message(reason, "That could not be saved."));
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

  const active = overview?.providers.find((provider) => provider.kind === overview.active);
  return <section className="service-settings model-settings tts-settings" aria-labelledby="tts-settings-heading">
    <div className="setting-heading">
      <Volume2 size={16} />
      <span><strong id="tts-settings-heading">Read aloud</strong><small>Have answers spoken to you. The first time an answer is read it is made and kept on this computer; after that it only plays.</small></span>
    </div>
    {!overview && !problem && <div className="setting-row"><span><strong>Loading…</strong><small>Asking the local service which voices are set up.</small></span></div>}
    {problem && <div className="setting-row is-problem"><span><strong><CircleAlert size={13} />Unavailable</strong><small>{problem}</small></span></div>}
    {overview && <>
      <label className="setting-row">
        <span><strong>Voice supplier</strong><small>Who turns the words into speech. More will be added.</small></span>
        <select value={overview.active ?? ""} aria-label="Voice supplier" onChange={(event) => void change({ active: event.target.value || null })}>
          <option value="">Off</option>
          {overview.providers.map((provider) => <option key={provider.kind} value={provider.kind}>{provider.name}</option>)}
        </select>
      </label>
      {overview.active && overview.problem && <p className="model-role-problem tts-problem"><CircleAlert size={12} />{overview.problem}</p>}
      {active && <div className="tts-panel"><ProviderEditor key={`${active.kind}-${active.keySet}-${active.model}`} provider={active} onOverview={setOverview} onNotify={onNotify} /></div>}
      {active && <label className="setting-row">
        <span><strong>Read new answers automatically</strong><small>Like a conversation: when an answer is finished it is spoken, and you can pause it from the answer.</small></span>
        <input type="checkbox" role="switch" aria-label="Read new answers automatically" checked={overview.autoRead} onChange={(event) => void change({ autoRead: event.target.checked })} />
      </label>}
      {active && <p className="library-folder-note">{active.readsStructure
        ? `${active.name}'s voice model reads tables and pictures itself.`
        : "Tables, pictures, code and formulas are described in words by your language model before they are spoken, instead of being read cell by cell."}</p>}
      <div className="setting-row">
        <span><strong>Saved recordings</strong><small>{overview.cache.clips === 0 ? "None yet." : `${overview.cache.clips} ${overview.cache.clips === 1 ? "answer" : "answers"} · ${megabytes(overview.cache.bytes)}. Deleting them only means answers are made again the next time.`}</small></span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={clearing || overview.cache.clips === 0} onClick={() => void clear()}>Delete</button>
      </div>
      {!overview.persisted && <p className="library-folder-note">This backend has no settings file, so changes last until it restarts.</p>}
    </>}
  </section>;
}
