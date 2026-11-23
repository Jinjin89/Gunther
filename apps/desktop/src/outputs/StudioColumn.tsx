import { artifactAudiences, type BuildTurnInput, type OutputScope, type OutputType } from "@gunther/contracts";
import { BookOpen, CalendarDays, CircleHelp, ClipboardList, FileText, LoaderCircle, Pencil, Presentation, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { openSettings } from "../components/SettingsLayout";
import { useModelMenu } from "../models/askModel";
import { artifactRequestId } from "./artifactConversations";
import { outputActivity } from "./liveOutput";
import { latestVersions, type OutputTarget, type useStudio } from "./useStudio";

const icons = { BookOpen, CalendarDays, CircleHelp, ClipboardList, FileText, Presentation };
type Card = {
  id: string; title: string; label: string; at: string; target: OutputTarget; status: "saved" | "running" | "failed";
  detail: string; retry?: BuildTurnInput | undefined;
};

function cardsOf(studio: ReturnType<typeof useStudio>): Card[] {
  const label = (id: string | null | undefined) => studio.types.find((type) => type.id === id)?.label;
  const cards = new Map<string, Card>();
  const running = new Set<string>();
  const heads = latestVersions(studio.artifacts);
  for (const artifact of heads) {
    const id = artifact.lineageId;
    cards.set(id, {
      id, title: artifact.title, label: artifact.outputType, at: artifact.createdAt, status: "saved",
      target: { conversationId: studio.owner(artifact), lineageId: id, typeId: artifact.style },
      detail: `Version ${artifact.versionNumber}`,
    });
  }
  const showRun = (id: string, target: OutputTarget, title: string, detail: string, at: string) => {
    const conversation = studio.conversations.find((item) => item.id === id);
    const current = target.lineageId ? cards.get(target.lineageId) : undefined;
    const typeId = target.typeId ?? current?.target.typeId;
    const key = target.lineageId ?? id;
    running.add(id);
    cards.set(key, {
      id: key, title: current?.title ?? title,
      label: label(typeId) ?? current?.label ?? conversation?.outputType ?? "Output",
      at, status: "running", detail, target: { ...target, typeId: typeId ?? null },
    });
  };
  for (const [id, job] of studio.jobs) {
    const head = heads.find((item) => job.lineageId ? item.lineageId === job.lineageId
      : job.kind === "follow" && studio.owner(item) === id);
    if (!job.build && !head) continue;
    showRun(id, { conversationId: id, typeId: job.build?.type ?? null, lineageId: head?.lineageId ?? null },
      job.live.title || job.build?.title || job.build?.focus || label(job.build?.type) || "Output",
      job.title && !job.live.running.length ? job.title : outputActivity(job.live), job.startedAt);
  }
  for (const item of studio.activities) {
    if (item.state !== "running" && item.state !== "waiting") continue;
    const target = item.open;
    if (target?.view !== "library" || target.tab !== "outputs") continue;
    const id = target.conversationId ?? target.lineageId;
    if (id && !running.has(id)) showRun(id, target, item.title, item.detail || "Starting…", item.startedAt);
  }
  const showFailure = (id: string, build: BuildTurnInput, error: string, at: string) => {
    if (running.has(id)) return;
    const type = label(build.type) ?? "Output";
    cards.set(id, {
      id, title: build.title || build.focus || type, label: type, at,
      target: { conversationId: id, typeId: build.type }, status: "failed", detail: error, retry: build,
    });
  };
  for (const conversation of studio.conversations) {
    const last = conversation.lastTurn;
    if (last?.build && last.error) showFailure(conversation.id, last.build, last.error, last.createdAt);
  }
  for (const [id, failure] of Object.entries(studio.failures)) {
    if (failure?.build) showFailure(id, failure.build, failure.message, failure.createdAt);
  }
  return [...cards.values()].sort((a, b) => b.at.localeCompare(a.at));
}

function BuildOptions({ type, disabled, onBuild, onClose }: {
  type: OutputType; disabled: boolean; onBuild: (options: BuildTurnInput) => void; onClose: () => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (disabled) return;
    const data = new FormData(event.currentTarget);
    const options: BuildTurnInput = { type: type.id };
    for (const field of type.options) {
      const value = String(data.get(field) ?? "").trim();
      if (value) Object.assign(options, { [field]: value });
    }
    onBuild(options);
  };
  return <form className="studio-options" aria-label={`${type.label} options`} onSubmit={submit} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <header><strong>{type.label}</strong><button type="button" className="gx-icon-button" aria-label="Close options" onClick={onClose}><X size={15} /></button></header>
    {type.options.includes("focus") && <label>Focus<textarea name="focus" rows={2} maxLength={2_000} autoFocus /></label>}
    {type.options.includes("audience") && <label>Audience<select name="audience"><option value="">Default</option>{artifactAudiences.map((audience) => <option key={audience} value={audience}>{audience[0]!.toUpperCase() + audience.slice(1)}</option>)}</select></label>}
    {type.options.includes("length") && <label>Length<select name="length"><option value="">Default</option><option value="short">Short</option><option value="standard">Standard</option><option value="long">Long</option></select></label>}
    {type.options.includes("language") && <label>Language<input name="language" maxLength={80} placeholder="Automatic" /></label>}
    {type.options.includes("use") && <label>Use<select name="use"><option value="">Default</option><option value="talk">Present</option><option value="read">Read</option></select></label>}
    <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={disabled}>Build {type.label}</button>
  </form>;
}

export function StudioColumn({ studio, scope, scopeEmpty, disabled, onOpen }: {
  studio: ReturnType<typeof useStudio>; scope: OutputScope; scopeEmpty: boolean; disabled: boolean;
  onOpen: (target: OutputTarget) => void;
}) {
  const [optionsId, setOptionsId] = useState<string | null>(null);
  const { menu, unavailable } = useModelMenu();
  const needsModel = menu !== null && menu.models.length === 0;
  const buildDisabled = disabled || unavailable || !menu?.models.length;
  const cards = cardsOf(studio);
  const optionType = studio.types.find((type) => type.id === optionsId);
  const closeOptions = () => setOptionsId(null);
  const tilesDisabled = buildDisabled || scopeEmpty;
  const start = (options: BuildTurnInput) => {
    if (tilesDisabled) return;
    closeOptions();
    void studio.build(artifactRequestId(), { ...options, scope });
  };
  return <aside className="studio-column" aria-label="Studio">
    {studio.error && <p role="alert">{studio.error}</p>}
    {unavailable ? <p role="alert">Studio models could not be loaded.</p> : needsModel && <div className="studio-model-note">
      <p role="status">Studio needs a model.</p>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("models")}>Open Models settings</button>
    </div>}
    <div className="studio-tiles">{studio.types.map((type) => {
      const Icon = icons[type.icon as keyof typeof icons] ?? FileText;
      const running = cards.find((card) => card.status === "running" && card.target.typeId === type.id && !card.target.lineageId);
      return <div key={type.id} className="studio-tile">
        <button type="button" title={type.description} disabled={tilesDisabled} onClick={() => running ? onOpen(running.target) : start({ type: type.id })}><Icon size={16} /><span>{type.label}</span></button>
        <button type="button" className="gx-icon-button" disabled={tilesDisabled} aria-label={`${type.label} options`}
          aria-expanded={optionType?.id === type.id} onClick={() => setOptionsId(optionsId === type.id ? null : type.id)}><Pencil size={13} /></button>
      </div>;
    })}</div>
    {optionType && <div className="studio-popover"><BuildOptions key={optionType.id} type={optionType}
      disabled={tilesDisabled} onBuild={start} onClose={closeOptions} /></div>}
    <ul className="studio-cards">{cards.map((card) => <li key={card.id}>
      <button type="button" className="studio-card-open" onClick={() => onOpen(card.target)}>
        {card.status === "running" ? <LoaderCircle size={16} className="spin" /> : <FileText size={16} />}
        <span><strong>{card.title}</strong><small>{card.label}</small><small className={card.status === "failed" ? "is-failed" : ""}>{card.detail}</small></span>
      </button>
      {card.retry && card.target.conversationId && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={buildDisabled} onClick={() => void studio.retry(card.target.conversationId!, card.retry!)}>Retry</button>}
    </li>)}</ul>
  </aside>;
}
