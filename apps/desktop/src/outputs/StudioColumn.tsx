import type { ActivityItem, OutputScope, OutputType, PlanStatus } from "@gunther/contracts";
import { LoaderCircle, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import type { MenuItem } from "../contents/SectionMenu";
import { ArtifactMenu } from "./ArtifactMenu";
import { outputActivityTarget, outputTargetKey, planAddress } from "./artifactConversations";
import { outputActivity } from "./liveOutput";
import { outputIcon, resolveOutputType } from "./outputCatalog";
import { latestVersions, type OutputTarget, type OutputTrashTarget, type PlanAddress, type SourceState, type useStudio } from "./useStudio";

export type StudioRun = { activity: Pick<ActivityItem, "id" | "startedAt"> | undefined };

const statusLabels = {
  setup: "Setup", planning: "Planning", needs_answer: "Needs answer", ready: "Ready to approve",
  generating: "Generating", failed: "Failed", saved: "",
} satisfies Record<"setup" | PlanStatus, string>;

type Card = {
  id: string;
  title: string;
  label: string;
  at: string;
  target: OutputTarget;
  type: OutputType | undefined;
  status: keyof typeof statusLabels;
  detail?: string | undefined;
  version?: number;
  trash?: OutputTrashTarget;
  running?: { run: StudioRun; stop: (() => Promise<void>) | undefined; stopping?: boolean };
};

const cardKey = (target: OutputTarget) =>
  target.lineageId ? outputTargetKey({ lineageId: target.lineageId }) : outputTargetKey(target);

function cardsOf(studio: ReturnType<typeof useStudio>): Card[] {
  const typeOf = (id: string | null | undefined) => studio.types.find((type) => type.id === id);
  const cards = new Map<string, Card>();
  const heads = latestVersions(studio.artifacts);
  const settledPlans = [...studio.jobs.values()].flatMap((job) => {
    const event = job.live.plan;
    return event && event.plan.planStatus !== "saved" && event.plan.attempt?.state !== "running"
      ? [{ conversationId: event.conversationId, planId: event.plan.planId }] : [];
  });
  const active = studio.activities.flatMap((item) => {
    const target = outputActivityTarget(item.open);
    return target && (item.state === "running" || item.state === "waiting") &&
      !settledPlans.some((plan) => plan.conversationId === target.conversationId && plan.planId === target.planId)
      ? [{ item, target }] : [];
  });

  for (const artifact of heads) {
    const conversationId = studio.owner(artifact);
    const work = studio.works.find((item) => item.conversationId === conversationId && item.lineageId === artifact.lineageId);
    const type = resolveOutputType(studio.types, artifact) ?? studio.types.find((item) => item.shape === artifact.kind);
    const target = { conversationId, lineageId: artifact.lineageId, typeId: type?.id ?? artifact.style, planId: work?.planId ?? null };
    const id = cardKey(target)!;
    cards.set(id, {
      id, title: artifact.title, label: artifact.outputType, at: artifact.createdAt, status: "saved", type, target,
      version: artifact.versionNumber, trash: { id: artifact.id, lineageId: artifact.lineageId },
    });
  }

  for (const draft of Object.values(studio.planning.locals)) {
    if (!draft) continue;
    const type = typeOf(draft.setup.type);
    const label = type?.label ?? "Output";
    const target = { conversationId: draft.conversationId, planId: draft.planId, typeId: draft.setup.type };
    const id = cardKey(target)!;
    cards.set(id, { id, title: draft.setup.title || label, label, at: draft.updatedAt, target, type, status: "setup", detail: "This device" });
  }

  for (const work of studio.works) {
    const target = { conversationId: work.conversationId, planId: work.planId ?? work.workKey, typeId: work.type };
    if (work.status === "saved" || work.versionId || studio.savedForPlan(target)) continue;
    const type = typeOf(work.type);
    const label = type?.label ?? "Output";
    const id = cardKey(target)!;
    const attempt = work.attempt;
    const running = attempt?.state === "running" && !studio.activities.some((item) => {
      const open = outputActivityTarget(item.open);
      return (item.state === "done" || item.state === "failed") && open?.conversationId === target.conversationId
        && open.planId === target.planId && item.endedAt && item.endedAt >= work.updatedAt;
    });
    const status = !running && work.status === "generating" ? "planning" : work.status;
    const detail = status === "failed"
      ? [attempt?.phase === "generate" ? "Generation failed" : "Planning failed", attempt?.detail].filter(Boolean).join(" · ")
      : attempt?.detail || (attempt?.state === "stopped" ? "Paused" : attempt?.state === "interrupted" ? "Interrupted"
        : status === "planning" && !running ? "Update plan" : undefined);
    cards.set(id, {
      id, title: work.title || label, label, at: work.updatedAt, target, type, status, detail,
      ...(running ? { running: { run: { activity: undefined }, stop: () => studio.turnStop(work.conversationId) } }
        : { trash: { id: work.conversationId, conversation: true } as const }),
    });
  }

  const showRun = (target: OutputTarget, title: string, detail: string, at: string, running: NonNullable<Card["running"]>, generating?: boolean) => {
    const address = planAddress(target);
    const verified = target.artifactId ? studio.artifacts.find((item) => item.id === target.artifactId) : undefined;
    const saved = address ? studio.savedForPlan(address) : undefined;
    const lineageId = target.lineageId ?? verified?.lineageId ?? saved?.lineageId ?? null;
    const artifactId = target.artifactId ?? saved?.id ?? null;
    const id = cardKey({ ...target, lineageId });
    if (!id) return;
    const previous = cards.get(id);
    // A committing plan and its first version share a row while any attachment finishes.
    if (address && lineageId) cards.delete(cardKey(address)!);
    const type = typeOf(target.typeId) ?? previous?.type;
    const isGenerating = generating ?? (previous?.status === "generating" || Boolean(lineageId || artifactId));
    cards.set(id, {
      id, title: previous?.title ?? title, label: previous?.label ?? type?.label ?? "Output", type, at,
      target: { ...target, conversationId: target.conversationId ?? (verified ? studio.owner(verified) : null), lineageId, artifactId },
      status: isGenerating ? "generating" : "planning", detail, running,
    });
  };

  for (const { item, target } of active) {
    showRun(target, item.about || item.title, item.detail || "Starting…", item.startedAt, {
      run: { activity: item }, stop: item.canStop ? () => knowledgeApi.stopActivity(item.id) : undefined,
    });
  }
  for (const [conversationId, job] of studio.jobs) {
    const plan = job.live.plan?.plan;
    if (settledPlans.some((settled) => settled.conversationId === conversationId && settled.planId === plan?.planId)) continue;
    const planId = job.planId ?? plan?.planId;
    const announced = active.find(({ target }) => target.conversationId === conversationId
      && (!planId || target.planId === planId)
      && (!job.lineageId || !target.lineageId || target.lineageId === job.lineageId)
      && (!job.artifactId || !target.artifactId || target.artifactId === job.artifactId));
    const lineageId = job.lineageId ?? announced?.target.lineageId;
    const artifactId = job.artifactId ?? announced?.target.artifactId;
    if (!planId && !announced && !lineageId && !artifactId) continue;
    const typeId = job.typeId ?? plan?.options.type ?? announced?.target.typeId;
    const detail = job.stopping ? "Stopping…" : job.live.running.length ? outputActivity(job.live)
      : job.title ?? announced?.item.detail ?? "Starting…";
    showRun({ conversationId, lineageId: lineageId ?? null, artifactId: artifactId ?? null, planId: planId ?? null, typeId: typeId ?? null },
      job.live.title || typeOf(typeId)?.label || "Output", detail, job.startedAt,
      { run: { activity: announced?.item }, stop: () => studio.stop(conversationId), stopping: job.stopping },
      job.phase === "generate" || plan?.attempt?.phase === "generate" ? true : undefined);
  }
  return [...cards.values()].sort((a, b) => b.at.localeCompare(a.at) || a.id.localeCompare(b.id));
}

export function StudioColumn({ studio, scope, sourceState, sourceCount, disabled, activeOutput, onOpen, onTrash, onDiscard, onNotify }: {
  studio: ReturnType<typeof useStudio>; scope: OutputScope; sourceState: SourceState; sourceCount: number; disabled: boolean;
  activeOutput: OutputTarget | null;
  onOpen: (target: OutputTarget | (() => OutputTarget), run?: StudioRun) => boolean;
  onTrash: (target: OutputTrashTarget, reopen: OutputTarget) => void;
  onDiscard: (address: PlanAddress) => void;
  onNotify: (message: string) => void;
}) {
  const cards = cardsOf(studio);
  const activeKey = activeOutput && cardKey(activeOutput);
  const activePlan = activeOutput && planAddress(activeOutput);
  const canFold = cards.length > 0;
  const [expanded, setExpanded] = useState(false);
  if (!canFold && expanded) setExpanded(false);
  const tilesId = useId();
  const disclosure = useRef<HTMLButtonElement>(null);
  const pendingDisclosureFocus = useRef(false);
  useEffect(() => {
    if (!pendingDisclosureFocus.current) return;
    pendingDisclosureFocus.current = false;
    const button = disclosure.current;
    if (canFold && !expanded && button && !button.closest("[hidden]")) button.focus({ preventScroll: true });
  }, [canFold, expanded]);

  const openTile = (type: OutputType, fresh = false) => {
    const preferred = activeOutput?.planId ?? undefined;
    const existing = !fresh ? studio.planning.existingFor?.(type.id, preferred) ?? null : null;
    const opened = onOpen(existing
      ? { ...existing, typeId: type.id }
      : () => ({
        ...studio.planning.targetFor(type.id, scope, {
          fresh: true, ...(preferred ? { preferredPlanId: preferred } : {}),
        }),
        typeId: type.id,
      }));
    if (!opened) return;
    setExpanded(false);
    pendingDisclosureFocus.current = !fresh;
  };
  const scopeLabel = sourceState === "error" ? "Source count unavailable"
    : sourceState === "loading" ? "Loading sources…"
    : sourceState === "empty" ? "Add sources to plan outputs."
    : `Using ${scope.mode === "library" ? "all " : ""}${sourceCount} ${scope.mode === "selection" ? "selected " : ""}${sourceCount === 1 ? "source" : "sources"}`;

  return <aside className="studio-column" aria-label="Studio">
    {studio.error && <p role="alert">{studio.error}</p>}
    <p className="studio-scope">{scopeLabel}</p>
    {studio.types.length > 0 && <div className="studio-new">
      {canFold && <button ref={disclosure} type="button" className="gx-btn gx-btn-quiet gx-btn-sm studio-disclosure"
        aria-expanded={expanded} aria-controls={tilesId} onClick={() => setExpanded((value) => !value)}>New output</button>}
      <div className="studio-tiles" id={tilesId} hidden={canFold && !expanded}>{studio.types.map((type) => {
        const Icon = outputIcon(type);
        return <div key={type.id} className="studio-tile">
          <button type="button" title={type.description} disabled={disabled} onClick={() => openTile(type)}>
            <Icon size={16} aria-hidden="true" /><span>{type.label}</span>
          </button>
        </div>;
      })}</div>
    </div>}
    <ul className="studio-cards" aria-label="Outputs">{cards.map((card) => {
      const address = planAddress(card.target);
      const selected = card.id === activeKey || (address && activePlan?.planId === address.planId
        && activePlan.conversationId === address.conversationId);
      const open = () => onOpen(card.target, card.running?.run);
      const Icon = card.running ? LoaderCircle : outputIcon(card.type);
      const menu: MenuItem[] = [];
      if (card.status === "planning" && !card.running) menu.push({ label: "Resume", onSelect: open });
      if (address && !card.target.lineageId && !card.target.artifactId && card.type) {
        const type = card.type;
        menu.push({ label: `New ${type.label} plan`, onSelect: () => openTile(type, true) });
      }
      const trash = card.trash;
      if (trash) menu.push({ label: "Move to Trash", icon: Trash2, danger: true, onSelect: () => onTrash(trash, card.target) });
      const stop = card.running?.stop;
      return <li key={card.id}>
        <button type="button" className="studio-card-open" aria-current={selected ? "page" : undefined} onClick={open}>
          <Icon size={16} strokeWidth={1.75} className={card.running ? "spin" : undefined} aria-hidden="true" />
          <span>
            <strong>{card.title}</strong>
            {card.status === "saved" ? <small>
              {card.label}{" "}<span aria-hidden="true">/ v{card.version}</span>
              <span className="gx-sr-only">Version {card.version}</span>
            </small> : <small>
              <span className="studio-card-state" data-state={card.status}>{statusLabels[card.status]}</span>
              {card.detail && <> · {card.detail}</>}
            </small>}
          </span>
        </button>
        {stop && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label={`Stop ${card.title}`}
          disabled={card.running?.stopping} onClick={() => void stop().catch((reason) =>
            onNotify(reason instanceof Error ? reason.message : "The output could not be stopped."))}>Stop</button>}
        {card.status === "setup" && address && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
          aria-label={`Discard ${card.title} setup`} onClick={() => onDiscard(address)}>Discard</button>}
        {menu.length > 0 && <ArtifactMenu label={`Actions for “${card.title}”`} items={menu} />}
      </li>;
    })}</ul>
  </aside>;
}
