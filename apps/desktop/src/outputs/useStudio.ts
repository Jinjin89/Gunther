import type {
  Artifact, ArtifactConversationDetail, ArtifactConversationSummary, ArtifactSummary, ArtifactTurnOut, CreationTurn,
  OutputActivityOpen, OutputScope, OutputType, OutputTypeId, PlanSetup, SavedArtifactTurnInput, WorkPlanPatch,
} from "@gunther/contracts";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useActivity } from "../activity/useActivity";
import { ApiRequestError, isTurnResult, knowledgeApi, OutputStoppedError, ServiceUnavailableError, type ArtifactTurnEvent, type OutputDone } from "../api";
import { artifactRequestId, conversationWorks, EARLIER_WORK, importDeviceConversations, outputActivityTarget, planAddress, planAddressKey } from "./artifactConversations";
import { useLiveOutputs, type PlanEvent } from "./liveOutput";
import { useStudioDrafts } from "./studioDrafts";

export type PlanAddress = { conversationId: string; planId: string };
export type OutputTarget = Pick<OutputActivityOpen, "artifactId" | "conversationId" | "lineageId" | "typeId" | "planId">;
export type SourceState = "loading" | "error" | "empty" | "ready";
export type LocalSetup = PlanAddress & { setup: PlanSetup; firstRequestId: string; updatedAt: string; touched: boolean };
export interface StudioPlanning {
  locals: Readonly<Record<string, LocalSetup | undefined>>;
  existingFor?(type: OutputTypeId, preferredPlanId?: string): PlanAddress | null;
  targetFor(type: OutputTypeId, scope: OutputScope, options?: { preferredPlanId?: string; fresh?: boolean }): PlanAddress;
  setLocal(draft: LocalSetup): void;
  discardLocal(planId: string): void;
  patch(conversationId: string, workKey: string, input: WorkPlanPatch): Promise<ArtifactConversationDetail>;
  turn(conversationId: string, input: CreationTurn): Promise<ArtifactTurnOut>;
}

export type OutputTrashTarget = { id: string; lineageId: string } | { id: string; conversation: true };
type SavedTurn = Omit<SavedArtifactTurnInput, "clientRequestId">;
export type OutputListener = (hear: (event: ArtifactTurnEvent) => void, signal: AbortSignal) => Promise<OutputDone | null>;

interface CommittedPlan {
  event: PlanEvent;
  order: number;
  updatedAt: string;
}

const workPlanId = (work: { workKey: string; planId: string | null }) => work.planId ?? work.workKey;

function workObservation(detail: ArtifactConversationDetail, key: string) {
  const work = detail.works.find((item) => item.key === key);
  return JSON.stringify([detail.editVersion ?? 0, detail.brief, work]);
}

export function latestVersions(versions: ArtifactSummary[]): ArtifactSummary[] {
  const heads = new Map<string, ArtifactSummary>();
  for (const version of versions) {
    if ((heads.get(version.lineageId)?.versionNumber ?? -1) < version.versionNumber) heads.set(version.lineageId, version);
  }
  return [...heads.values()];
}

export function useStudio(baseId: string, workspaceId: string | null) {
  const { items } = useActivity();
  const activities = items.filter((item) => outputActivityTarget(item.open)?.libraryId === baseId);
  const [types, setTypes] = useState<OutputType[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [artifactsLoaded, setArtifactsLoaded] = useState(false);
  const [failures, setFailures] = useState<Record<string, { message: string } | undefined>>({});
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const { jobs, watch, stop, reset } = useLiveOutputs();
  const { store: drafts, setLocal, discardLocal } = useStudioDrafts(baseId, workspaceId);
  const loadOrder = useRef(0);
  const [, redraw] = useReducer((revision: number) => revision + 1, 0);
  const cache = useMemo(() => ({
    details: {} as Record<string, ArtifactConversationDetail>,
    conversations: [] as ArtifactConversationSummary[],
    orders: new Map<string, number>(),
    trashed: new Map<string, number>(),
    plans: new Map<string, CommittedPlan>(),
    saved: new Map<string, ArtifactSummary>(),
    patches: new Map<string, Set<Promise<ArtifactConversationDetail>>>(),
    order: 0,
  }), [baseId, workspaceId]);

  const acknowledge = useCallback(({ conversationId, planId }: PlanAddress) => {
    if (drafts.locals[planId]?.conversationId === conversationId) discardLocal(planId);
  }, [drafts, discardLocal]);
  const acknowledgeWorks = useCallback((conversations: ArtifactConversationSummary[]) => {
    for (const work of conversationWorks(conversations)) {
      if (work.planId) acknowledge({ conversationId: work.conversationId, planId: work.planId });
    }
  }, [acknowledge]);

  const remember = useCallback((detail: ArtifactConversationDetail, order = ++cache.order) => {
    const { id } = detail;
    const current = cache.details[id];
    const version = detail.editVersion ?? 0;
    const currentVersion = current?.editVersion ?? 0;
    const currentOrder = cache.orders.get(id) ?? 0;
    if (order <= (cache.trashed.get(id) ?? 0) || version < currentVersion
      || (current && version === currentVersion && (detail.updatedAt < current.updatedAt
        || (detail.updatedAt === current.updatedAt && order < currentOrder)))) return current ?? null;

    let plans = cache.plans;
    for (const [key, { event, order: seen }] of cache.plans) {
      if (event.conversationId !== id || version < event.editVersion || (version === event.editVersion && order < seen)) continue;
      if (!detail.works.some((work) => work.plan?.planId === event.plan.planId)) continue;
      if (plans === cache.plans) plans = new Map(cache.plans);
      plans.delete(key);
    }
    cache.plans = plans;
    cache.details = { ...cache.details, [id]: detail };
    const summary = cache.conversations.find((item) => item.id === id);
    if (!summary || detail.updatedAt > summary.updatedAt || (detail.updatedAt === summary.updatedAt && order >= currentOrder)) {
      cache.conversations = [detail, ...cache.conversations.filter((item) => item.id !== id)];
    }
    cache.orders.set(id, Math.max(currentOrder, order));
    acknowledgeWorks([detail]);
    for (const work of detail.works) {
      if (work.plan) acknowledge({ conversationId: id, planId: work.plan.planId });
    }
    redraw();
    return detail;
  }, [cache, acknowledge, acknowledgeWorks]);

  const rememberVersion = useCallback((artifact: Artifact) => {
    loadOrder.current += 1;
    setArtifacts((current) => [artifact, ...current.filter((item) => item.id !== artifact.id)]);
  }, []);

  const forgetTrashed = (target: OutputTrashTarget) => {
    loadOrder.current += 1;
    if ("conversation" in target) {
      cache.trashed.set(target.id, ++cache.order);
      cache.conversations = cache.conversations.filter((item) => item.id !== target.id);
      const details = { ...cache.details };
      delete details[target.id];
      cache.details = details;
      cache.plans = new Map([...cache.plans].filter(([, { event }]) => event.conversationId !== target.id));
      setArtifacts((current) => current.filter((item) => item.conversationId !== target.id));
      setFailures((current) => ({ ...current, [target.id]: undefined }));
    } else {
      setArtifacts((current) => current.filter((item) => item.lineageId !== target.lineageId));
    }
    redraw();
  };

  const reload = useCallback(async () => {
    if (!workspaceId) return;
    const order = ++loadOrder.current;
    try {
      const versions = await knowledgeApi.artifacts(baseId, workspaceId);
      if (order !== loadOrder.current) return;
      setArtifacts(versions);
      setArtifactsLoaded(true);
      const importError = await importDeviceConversations(baseId, workspaceId, versions).catch(() => "Earlier work could not be imported.");
      const listOrder = ++cache.order;
      const list = await knowledgeApi.artifactConversations(baseId, workspaceId);
      if (order !== loadOrder.current) return;
      const merged = new Map(list.map((item) => [item.id, item]));
      for (const current of cache.conversations) {
        const incoming = merged.get(current.id);
        if ((cache.orders.get(current.id) ?? 0) > listOrder || (incoming && incoming.updatedAt < current.updatedAt)) {
          merged.set(current.id, current);
        }
      }
      cache.conversations = [...merged.values()];
      acknowledgeWorks(cache.conversations);
      redraw();
      setError(importError ?? "");
    } catch (reason) {
      if (order === loadOrder.current) setError(reason instanceof Error ? reason.message : "Could not load outputs.");
    }
  }, [baseId, workspaceId, cache, acknowledgeWorks]);

  useEffect(() => {
    const abort = new AbortController();
    void knowledgeApi.outputTypes(abort.signal).then((catalog) => {
      if (!abort.signal.aborted) setTypes(catalog);
    }).catch((reason) => {
      if (!abort.signal.aborted) setCatalogError(reason instanceof Error ? reason.message : "Could not load Studio types.");
    });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    setArtifacts([]); setArtifactsLoaded(false); setFailures({});
    void reload();
    return () => { loadOrder.current += 1; reset(); };
  }, [reload, reset]);
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener("gunther:trash-updated", refresh);
    window.addEventListener("gunther:sources-updated", refresh);
    return () => {
      window.removeEventListener("gunther:trash-updated", refresh);
      window.removeEventListener("gunther:sources-updated", refresh);
    };
  }, [reload]);
  const activityChanges = activities.filter((item) => item.state === "done" || item.state === "failed" || outputActivityTarget(item.open)?.planId)
    .map((item) => [item.id, item.startedAt, item.endedAt, item.open, item.state]);
  const activityKey = activityChanges.length ? JSON.stringify(activityChanges) : "";
  useEffect(() => { if (activityKey) void reload(); }, [activityKey, reload]);

  const read = useCallback(async (id: string) => {
    if (!workspaceId) return null;
    const order = ++cache.order;
    try { return remember(await knowledgeApi.artifactConversation(baseId, id, workspaceId), order); }
    catch (reason) {
      if (reason instanceof ApiRequestError && reason.status === 404) return null;
      throw reason;
    }
  }, [baseId, workspaceId, cache, remember]);

  const commitPlan = useCallback((event: PlanEvent, runOrder: number, requestId?: string) => {
    const address = { conversationId: event.conversationId, planId: event.plan.planId };
    const key = planAddressKey(address);
    const detail = cache.details[address.conversationId];
    const previous = cache.plans.get(key);
    const version = Math.max(detail?.editVersion ?? 0, previous?.event.editVersion ?? 0);
    const current = previous?.event.plan ?? detail?.works.find((work) => work.plan?.planId === address.planId)?.plan;
    const order = Math.max(cache.orders.get(address.conversationId) ?? 0, previous?.order ?? 0);
    if (runOrder <= (cache.trashed.get(address.conversationId) ?? 0) || event.editVersion < version) return false;
    if (event.editVersion === version && runOrder < order && current) {
      const latestRequest = current.attempt?.requestId ?? [...(detail?.messages ?? [])].reverse()
        .find((message) => message.role === "user" && message.payload.planId === address.planId)?.clientRequestId;
      const committedRequest = event.plan.attempt?.requestId ?? requestId;
      if ((latestRequest && committedRequest && latestRequest !== committedRequest)
        || (current.attempt?.state !== "running" && event.plan.attempt?.state === "running")) return false;
    }
    cache.plans = new Map(cache.plans).set(key, { event, order: ++cache.order, updatedAt: new Date().toISOString() });
    acknowledge(address);
    redraw();
    return true;
  }, [cache, acknowledge]);

  const turnStop = useCallback((id: string) => {
    if (!workspaceId) return Promise.resolve();
    return knowledgeApi.stopArtifactTurn(baseId, id, workspaceId);
  }, [baseId, workspaceId]);

  const run = useCallback(async (id: string, info: Parameters<typeof watch>[1], listen: OutputListener) => {
    const listener = watch(id, info);
    if (!listener) {
      if (info.phase) throw new ApiRequestError("Gunther is still working in this conversation.", 409, "run_active");
      return null;
    }
    const runOrder = ++cache.order;
    if (info.kind !== "follow" && !info.phase) setFailures((current) => ({ ...current, [id]: undefined }));
    try {
      const done = await listen((event) => {
        if (listener.signal.aborted) return;
        if (event.type === "turn" && info.phase && event.conversationId === id) {
          void read(id).catch(() => undefined);
        }
        if (event.type === "plan") {
          if (event.conversationId !== id || (info.planId && info.planId !== event.plan.planId)
            || (info.requestId && event.plan.attempt && info.requestId !== event.plan.attempt.requestId)) return;
          if (!commitPlan(event, runOrder, info.requestId)) return;
          if (event.plan.planStatus === "saved") void reload();
          void read(id).catch(() => undefined);
        }
        listener.hear(event);
      }, listener.signal);
      if (listener.signal.aborted) {
        if (info.phase) throw listener.signal.reason;
        return null;
      }
      if (!done) return null;
      const { artifact, plan } = isTurnResult(done) ? done : { artifact: done, plan: null };
      if (info.planId && plan && info.planId !== plan.planId) throw new ApiRequestError("The service returned a different plan.", 502);
      // A replay can end with historical plan data. Only committed progress and a fresh read update the editor.
      if (plan) acknowledge({ conversationId: id, planId: plan.planId });
      if (artifact) {
        rememberVersion(artifact);
        const planId = (info.phase === "generate" ? info.planId : undefined)
          ?? plan?.planId ?? artifact.provenance?.creationPlan?.planId;
        if (planId && artifact.conversationId === id) cache.saved.set(planAddressKey({ conversationId: id, planId }), artifact);
      }
      await Promise.allSettled([read(id), reload()]);
      return done;
    } catch (reason) {
      if (!listener.signal.aborted) {
        if (!info.phase && !(reason instanceof OutputStoppedError)) {
          const message = reason instanceof Error ? reason.message : "The request could not be completed.";
          setFailures((current) => ({ ...current, [id]: { message } }));
        }
        await Promise.allSettled([read(id), reload()]);
      }
      if (info.phase) throw reason;
      return null;
    } finally {
      listener.finish();
    }
  }, [watch, cache, commitPlan, acknowledge, rememberVersion, read, reload]);

  const follow = useCallback((id: string) => {
    if (!workspaceId) return;
    void run(id, { kind: "follow", stop: () => turnStop(id) },
      (hear, signal) => knowledgeApi.followArtifactTurn(baseId, id, workspaceId, hear, signal));
  }, [baseId, workspaceId, run, turnStop]);

  const ensure = async (id: string, scope: OutputScope) => {
    if (!workspaceId || cache.conversations.some((item) => item.id === id)) return;
    const order = ++cache.order;
    remember(await knowledgeApi.createArtifactConversation(baseId, { id, scope }, workspaceId), order);
  };
  const waitForPatches = useCallback(async (id: string, workKey: string | null | undefined) => {
    if (!workKey) return;
    const key = planAddressKey({ conversationId: id, planId: workKey });
    let pending = cache.patches.get(key);
    while (pending?.size) {
      await Promise.all(pending);
      pending = cache.patches.get(key);
    }
  }, [cache]);

  const send = async (id: string, turn: SavedTurn, scope?: OutputScope) => {
    if (!workspaceId) return null;
    await waitForPatches(id, turn.targetLineageId ?? cache.details[id]?.active);
    const turnId = artifactRequestId();
    return run(id, { kind: "turn", lineageId: turn.targetLineageId, stop: () => turnStop(id) }, async (hear, signal) => {
      if (scope) await ensure(id, scope);
      if (signal.aborted) return null;
      return knowledgeApi.artifactTurnStream(baseId, id, { ...turn, clientRequestId: turnId }, workspaceId, hear, signal);
    });
  };
  const direct = (id: string, artifact: Artifact, title: string, listen: OutputListener) =>
    run(id, { kind: "version", lineageId: artifact.lineageId, artifactId: artifact.id, title, stop: () => artifact.conversationId
      ? turnStop(id) : knowledgeApi.stopActivity(`output-lineage:${artifact.lineageId}`) }, listen);

  const patch = useCallback((id: string, workKey: string, input: WorkPlanPatch) => {
    if (!workspaceId) return Promise.reject(new ServiceUnavailableError());
    const key = planAddressKey({ conversationId: id, planId: workKey });
    const apply = async () => {
      const order = ++cache.order;
      const detail = await knowledgeApi.patchArtifactWork(baseId, id, workKey, input, workspaceId);
      const current = remember(detail, order);
      const superseded = [...cache.plans.values()].some(({ event, order: seen }) => event.conversationId === id
        && (event.editVersion > (detail.editVersion ?? 0) || (event.editVersion === detail.editVersion && seen > order)));
      if (!current || superseded || (current !== detail && workObservation(current, workKey) !== workObservation(detail, workKey))) {
        throw new ApiRequestError("The plan changed. Review its current fields.", 409, "edit_conflict");
      }
      return current;
    };
    const pending = cache.patches.get(key) ?? new Set<Promise<ArtifactConversationDetail>>();
    const previous = [...pending].at(-1);
    const result = previous ? previous.catch(() => undefined).then(apply) : apply();
    pending.add(result);
    cache.patches.set(key, pending);
    const clear = () => {
      pending.delete(result);
      if (!pending.size) cache.patches.delete(key);
    };
    void result.then(clear, clear);
    return result;
  }, [baseId, cache, remember, workspaceId]);

  const turn = useCallback(async (id: string, input: CreationTurn) => {
    if (!workspaceId) throw new ServiceUnavailableError();
    const work = cache.details[id]?.works.find((item) => item.plan?.planId === input.planId);
    await waitForPatches(id, work?.key ?? input.planId);
    const local = drafts.locals[input.planId];
    const typeId = "setup" in input ? input.setup.type : work?.plan?.options.type;
    if ("setup" in input && local?.conversationId === id && !local.touched) {
      setLocal({ ...local, touched: true, updatedAt: new Date().toISOString() });
    }
    const result = await run(id, {
      kind: "turn", typeId, planId: input.planId, requestId: input.clientRequestId,
      phase: input.kind === "approve" ? "generate" : "plan", stop: () => turnStop(id),
    }, (hear, signal) => knowledgeApi.artifactTurnStream(baseId, id, input, workspaceId, hear, signal));
    if (!result || !isTurnResult(result)) throw new ApiRequestError("The service did not return a planning result.", 502);
    return result;
  }, [baseId, workspaceId, drafts, setLocal, cache, turnStop, run, waitForPatches]);

  const conversations = cache.conversations;
  const details = useMemo(() => {
    let projected = cache.details;
    for (const { event } of cache.plans.values()) {
      const detail = projected[event.conversationId];
      if (!detail || event.editVersion < (detail.editVersion ?? 0)) continue;
      const index = detail.works.findIndex((work) => work.plan?.planId === event.plan.planId);
      const work = detail.works[index];
      if (!work) continue;
      const works = [...detail.works];
      works[index] = { ...work, plan: event.plan };
      projected = { ...projected, [detail.id]: { ...detail, works, editVersion: event.editVersion } };
    }
    return projected;
  }, [cache.details, cache.plans]);
  const works = conversationWorks(conversations);
  for (const { event, updatedAt } of cache.plans.values()) {
    const index = works.findIndex((work) => work.conversationId === event.conversationId && workPlanId(work) === event.plan.planId);
    const previous = works[index];
    const projected = {
      conversationId: event.conversationId, workKey: event.workKey, planId: event.plan.planId,
      planRevision: event.plan.planRevision, type: event.plan.options.type,
      title: event.plan.options.title || event.plan.proposal?.title || previous?.title || "",
      status: event.plan.planStatus, basis: event.plan.basis, attempt: event.plan.attempt, updatedAt,
      lineageId: event.plan.planStatus === "saved" ? event.workKey : null, versionId: previous?.versionId ?? null,
    };
    if (index < 0) works.push(projected);
    else works[index] = projected;
  }

  const owner = (artifact: ArtifactSummary) => artifact.conversationId
    || (conversations.some((item) => item.id === EARLIER_WORK) ? EARLIER_WORK : artifact.lineageId);

  const savedForPlan = (address: PlanAddress) => {
    const saved = cache.saved.get(planAddressKey(address));
    const work = works.find((item) => item.conversationId === address.conversationId && item.planId === address.planId);
    const announced = activities.map((item) => outputActivityTarget(item.open))
      .find((target) => target?.conversationId === address.conversationId && target.planId === address.planId && target.artifactId);
    const id = saved?.id ?? work?.versionId ?? announced?.artifactId;
    const lineage = saved?.lineageId ?? work?.lineageId ?? announced?.lineageId;
    return id ? artifacts.find((item) => item.id === id && owner(item) === address.conversationId)
      : lineage ? latestVersions(artifacts).find((item) => item.lineageId === lineage && owner(item) === address.conversationId)
        : undefined;
  };

  const existingFor: NonNullable<StudioPlanning["existingFor"]> = (type, preferredPlanId) => {
    const unfinished = new Map<string, PlanAddress & { updatedAt: string }>();
    const add = (address: PlanAddress, updatedAt: string) => {
      const key = planAddressKey(address);
      if (!unfinished.has(key) || unfinished.get(key)!.updatedAt < updatedAt) unfinished.set(key, { ...address, updatedAt });
    };
    // Read the current local store so a second click can reuse a just-allocated address.
    for (const draft of Object.values(drafts.locals)) if (draft?.setup.type === type) add(draft, draft.updatedAt);
    for (const work of works) {
      if (work.type === type && work.status !== "saved" && !work.versionId) {
        const address = { conversationId: work.conversationId, planId: workPlanId(work) };
        if (!savedForPlan(address)) add(address, work.updatedAt);
      }
    }
    for (const item of activities) {
      if (item.state !== "running" && item.state !== "waiting") continue;
      const target = outputActivityTarget(item.open);
      const address = target && planAddress(target);
      if (target?.typeId === type && address && !target.lineageId && !target.artifactId && !savedForPlan(address)) {
        add(address, item.startedAt);
      }
    }
    const candidates = [...unfinished.values()];
    const existing = candidates.find((item) => item.planId === preferredPlanId)
      ?? candidates.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || planAddressKey(a).localeCompare(planAddressKey(b)))[0];
    return existing ? { conversationId: existing.conversationId, planId: existing.planId } : null;
  };

  const targetFor: StudioPlanning["targetFor"] = (type, scope, options) => {
    if (!options?.fresh) {
      const existing = existingFor(type, options?.preferredPlanId);
      if (existing) return existing;
    }
    const address = { conversationId: artifactRequestId(), planId: `draft_${artifactRequestId()}` };
    setLocal({ ...address, setup: { type, scope: {
      ...scope, sourceIds: [...scope.sourceIds], unitIds: [...scope.unitIds], sessionIds: [...scope.sessionIds],
    } }, firstRequestId: artifactRequestId(), updatedAt: new Date().toISOString(), touched: false });
    return address;
  };
  const planning: StudioPlanning = { locals: drafts.locals, existingFor, targetFor, setLocal, discardLocal, patch, turn };

  return { types, artifacts, artifactsLoaded, conversations, details, works, activities, jobs, failures, error: catalogError || error,
    planning, remember, rememberVersion, forgetTrashed, savedForPlan, read, reload, follow, ensure, send, direct, owner, stop, turnStop };
}
