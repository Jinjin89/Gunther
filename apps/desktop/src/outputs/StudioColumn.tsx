import { artifactAudiences, type ActivityItem, type BuildTurnInput, type OutputScope, type OutputType } from "@gunther/contracts";
import { LoaderCircle, Pencil, Trash2, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { openSettings } from "../components/SettingsLayout";
import { useModelMenu } from "../models/askModel";
import { useEscape } from "../shortcuts/shortcuts";
import { ArtifactMenu } from "./ArtifactMenu";
import { artifactRequestId } from "./artifactConversations";
import { outputActivity } from "./liveOutput";
import { outputIcon, resolveOutputType } from "./outputCatalog";
import { StudioWebSwitch } from "./StudioWebSwitch";
import { latestVersions, type OutputTarget, type OutputTrashTarget, type useStudio } from "./useStudio";
import { readVoices, VoiceFields } from "./VoiceFields";

export type StudioRun = { activity: Pick<ActivityItem, "id" | "startedAt"> | undefined };

type Card = {
  id: string; title: string; label: string; at: string; target: OutputTarget; type: OutputType | undefined;
} & (
  | { status: "saved"; version: number; trash: OutputTrashTarget }
  | { status: "running"; detail: string; buildTypeId: string | null; run: StudioRun }
  | { status: "failed"; detail: string; retry: BuildTurnInput; trash: OutputTrashTarget }
);

function newBuildType({ lineageId, artifactId, typeId }: OutputTarget) {
  return lineageId || artifactId ? null : typeId ?? null;
}

function cardsOf(studio: ReturnType<typeof useStudio>): Card[] {
  const typeOf = (id: string | null | undefined) => studio.types.find((type) => type.id === id);
  const cards = new Map<string, Card>();
  const running = new Set<string>();
  const heads = latestVersions(studio.artifacts);
  const activities = new Map(studio.activities.flatMap((item) => {
    const target = item.open;
    if ((item.state !== "running" && item.state !== "waiting") || target?.view !== "library" || target.tab !== "outputs") return [];
    const id = target.conversationId ?? target.lineageId;
    return id ? [[id, { ...item, open: target }] as const] : [];
  }));
  for (const artifact of heads) {
    const id = artifact.lineageId;
    const type = resolveOutputType(studio.types, artifact) ?? studio.types.find((item) => item.shape === artifact.kind);
    cards.set(id, {
      id, title: artifact.title, label: artifact.outputType, at: artifact.createdAt, status: "saved", type,
      target: { conversationId: studio.owner(artifact), lineageId: id, typeId: type?.id ?? artifact.style },
      version: artifact.versionNumber,
      trash: { id: artifact.id, lineageId: id },
    });
  }
  const showRun = (id: string, target: OutputTarget, title: string, detail: string, at: string,
    activity: ActivityItem | undefined, buildTypeId = newBuildType(target)) => {
    const conversation = studio.conversations.find((item) => item.id === id);
    const current = target.lineageId ? cards.get(target.lineageId) : undefined;
    const type = typeOf(target.typeId) ?? current?.type;
    const key = target.lineageId ?? id;
    running.add(id);
    cards.set(key, {
      id: key, title: current?.title ?? title, type,
      label: current?.label ?? type?.label ?? conversation?.outputType ?? "Output",
      at, status: "running", detail, buildTypeId, run: { activity }, target: { ...target, typeId: type?.id ?? target.typeId ?? null },
    });
  };
  for (const [id, job] of studio.jobs) {
    const announced = activities.get(id);
    const open = announced?.open;
    const conflicts = (job.lineageId && open?.lineageId && job.lineageId !== open.lineageId)
      || (job.artifactId && open?.artifactId && job.artifactId !== open.artifactId);
    const activity = conflicts ? undefined : announced;
    const artifactId = job.artifactId ?? activity?.open.artifactId ?? null;
    const owned = heads.filter((item) => studio.owner(item) === id);
    const savedLineage = artifactId ? studio.artifacts.find((item) => item.id === artifactId)?.lineageId
      : owned.length === 1 ? owned[0]!.lineageId : null;
    const lineageId = job.lineageId ?? activity?.open.lineageId ?? savedLineage ?? null;
    if (!job.build && !activity && !lineageId && !artifactId) continue;
    // The tile follows the original build even after its output gets a lineage.
    const buildTypeId = job.build && !job.lineageId ? job.build.type
      : job.kind === "follow" && activity ? newBuildType(activity.open) : null;
    const detail = job.title && !job.live.running.length ? job.title : outputActivity(job.live);
    showRun(id, { conversationId: id, typeId: job.build?.type ?? activity?.open.typeId ?? null, lineageId, artifactId },
      job.live.title || job.build?.title || job.build?.focus || typeOf(job.build?.type)?.label || "Output",
      job.stopping ? "Stopping…" : detail, job.startedAt, activity, buildTypeId);
  }
  for (const [id, item] of activities) {
    if (!running.has(id)) showRun(id, item.open, item.title, item.detail || "Starting…", item.startedAt, item);
  }
  const showFailure = (id: string, build: BuildTurnInput, error: string, at: string) => {
    if (running.has(id)) return;
    const type = typeOf(build.type);
    const label = type?.label ?? "Output";
    cards.set(id, {
      id, title: build.title || build.focus || label, label, at, type,
      target: { conversationId: id, typeId: build.type }, status: "failed", detail: error, retry: build,
      trash: { id, conversation: true },
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

function BuildOptions({ type, disabled, webAvailable, onBuild, onClose }: {
  type: OutputType; disabled: boolean; webAvailable: boolean; onBuild: (options: BuildTurnInput) => void; onClose: () => void;
}) {
  useEscape(onClose);
  const [web, setWeb] = useState(false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (disabled) return;
    const data = new FormData(event.currentTarget);
    const options: BuildTurnInput = { type: type.id };
    if (webAvailable && web) options.web = true;
    for (const field of type.options) {
      if (field === "voices") {
        const voices = readVoices(data);
        if (voices) options.voices = voices;
        continue;
      }
      const value = String(data.get(field) ?? "").trim();
      if (value) Object.assign(options, { [field]: value });
    }
    onBuild(options);
  };
  return <form className="studio-options" aria-label={`${type.label} options`} onSubmit={submit}>
    <header><strong>{type.label}</strong><button type="button" className="gx-icon-button" aria-label="Close options" onClick={onClose}><X size={15} /></button></header>
    {type.options.includes("focus") && <label>Focus<textarea name="focus" rows={2} maxLength={2_000} autoFocus /></label>}
    {type.options.includes("audience") && <label>Audience<select name="audience"><option value="">Default</option>{artifactAudiences.map((audience) => <option key={audience} value={audience}>{audience[0]!.toUpperCase() + audience.slice(1)}</option>)}</select></label>}
    {type.options.includes("length") && <label>Length<select name="length"><option value="">Default</option><option value="short">Short</option><option value="standard">Standard</option><option value="long">Long</option></select></label>}
    {type.options.includes("language") && <label>Language<input name="language" maxLength={80} placeholder="Automatic" /></label>}
    {type.options.includes("use") && <label>Use<select name="use"><option value="">Default</option><option value="talk">Present</option><option value="read">Read</option></select></label>}
    {type.options.includes("voices") && type.voices && <VoiceFields options={type.voices} />}
    <StudioWebSwitch available={webAvailable} checked={web} onChange={setWeb} />
    <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={disabled}>Build {type.label}</button>
  </form>;
}

export function StudioColumn({ studio, scope, scopeEmpty, webAvailable, sourceCount, sourceLoadFailed, selectedSourceCount, disabled, activeOutput, onOpen, onTrash }: {
  studio: ReturnType<typeof useStudio>; scope: OutputScope; scopeEmpty: boolean; webAvailable: boolean;
  sourceCount: number | null; sourceLoadFailed: boolean; selectedSourceCount: number; disabled: boolean;
  activeOutput: Pick<OutputTarget, "lineageId" | "conversationId"> | null;
  onOpen: (target: OutputTarget, run?: StudioRun) => void;
  onTrash: (target: OutputTrashTarget) => void;
}) {
  const [optionsId, setOptionsId] = useState<string | null>(null);
  const { menu, unavailable } = useModelMenu();
  const needsModel = menu !== null && menu.models.length === 0;
  const buildDisabled = disabled || unavailable || !menu?.models.length;
  const cards = cardsOf(studio);
  const openCard = (card: Card) => onOpen(card.target, card.status === "running" ? card.run : undefined);
  const activeRun = activeOutput?.conversationId
    ? cards.find(({ target }) => !target.lineageId && target.conversationId === activeOutput.conversationId)
    : undefined;
  const activeCard = activeRun ?? cards.find(({ target }) => target.lineageId && target.lineageId === activeOutput?.lineageId);
  const optionType = studio.types.find((type) => type.id === optionsId);
  const canFold = cards.length >= 4;
  const [disclosure, setDisclosure] = useState({ canFold, open: false });
  if (disclosure.canFold !== canFold) setDisclosure({ canFold, open: Boolean(optionType) });
  const closeOptions = () => setOptionsId(null);
  const empty = sourceCount === 0;
  const tilesDisabled = buildDisabled || scopeEmpty || sourceCount === null || sourceLoadFailed;
  const start = (options: BuildTurnInput) => {
    if (tilesDisabled) return;
    closeOptions();
    void studio.build(artifactRequestId(), { ...options, scope });
  };
  const tiles = <><div className="studio-tiles">{studio.types.map((type) => {
      const Icon = outputIcon(type);
      const running = cards.find((card) => card.status === "running" && card.buildTypeId === type.id);
      return <div key={type.id} className="studio-tile">
        <button type="button" title={type.description} disabled={tilesDisabled && !running} onClick={() => running ? openCard(running) : start({ type: type.id })}><Icon size={16} /><span>{type.label}</span></button>
        <button type="button" className="gx-icon-button" disabled={tilesDisabled} aria-label={`${type.label} options`}
          aria-expanded={optionType?.id === type.id} onClick={() => setOptionsId(optionsId === type.id ? null : type.id)}><Pencil size={13} /></button>
      </div>;
    })}</div>
    {optionType && <div className="studio-popover"><BuildOptions key={optionType.id} type={optionType}
      disabled={tilesDisabled} webAvailable={webAvailable} onBuild={start} onClose={closeOptions} /></div>}</>;
  const count = scope.mode === "library" ? sourceCount : selectedSourceCount;
  const scopeLabel = sourceLoadFailed ? "Source count unavailable"
    : sourceCount === null ? "Loading sources…"
    : empty ? "Add sources to build outputs."
    : `Building from ${scope.mode === "library" ? "all " : ""}${count} ${scope.mode === "selection" ? "selected " : ""}${count === 1 ? "source" : "sources"}`;
  return <aside className="studio-column" aria-label="Studio">
    {studio.error && <p role="alert">{studio.error}</p>}
    <p className="studio-scope">{scopeLabel}</p>
    {!empty && <>
      {unavailable ? <p role="alert">Studio models could not be loaded.</p> : needsModel && <div className="studio-model-note">
        <p role="status">Studio needs a model.</p>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("models")}>Open Models settings</button>
      </div>}
      <details className="studio-new" open={!canFold || disclosure.open}>
        <summary hidden={!canFold} onClick={(event) => {
          event.preventDefault();
          if (disclosure.open) closeOptions();
          setDisclosure({ canFold, open: !disclosure.open });
        }}>New output</summary>
        {tiles}
      </details>
    </>}
    <ul className="studio-cards" aria-label="Outputs">{cards.map((card) => {
      const Icon = card.status === "running" ? LoaderCircle : outputIcon(card.type);
      return <li key={card.id}>
        <button type="button" className="studio-card-open" aria-current={card === activeCard ? "page" : undefined} onClick={() => openCard(card)}>
          <Icon size={16} strokeWidth={1.75} className={card.status === "running" ? "spin" : undefined} aria-hidden="true" />
          <span>
            <strong>{card.title}</strong>
            {card.status === "saved" ? <small>
              {card.label}{" "}<span aria-hidden="true">/ v{card.version}</span>
              <span className="gx-sr-only">Version {card.version}</span>
            </small> : <small className={card.status === "failed" ? "is-failed" : undefined}>
              <span className="gx-sr-only">{card.label}</span>{" "}{card.detail}
            </small>}
          </span>
        </button>
        {card.status === "failed" && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={buildDisabled} onClick={() => void studio.retry(card.id, card.retry)}>Retry</button>}
        {card.status !== "running" && <ArtifactMenu label={`Actions for “${card.title}”`} items={[
          { label: "Move to Trash", icon: Trash2, danger: true, onSelect: () => onTrash(card.trash) },
        ]} />}
      </li>;
    })}</ul>
  </aside>;
}
