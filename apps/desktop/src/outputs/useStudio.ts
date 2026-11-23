import type {
  Artifact, ArtifactConversationDetail, ArtifactConversationSummary, ArtifactSummary, ArtifactTurnInput,
  BuildTurnInput, OutputActivityOpen, OutputScope, OutputType,
} from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { useActivity } from "../activity/useActivity";
import { ApiRequestError, isTurnResult, knowledgeApi, OutputStoppedError, type ArtifactTurnEvent, type OutputDone } from "../api";
import { artifactRequestId, EARLIER_WORK, importDeviceConversations } from "./artifactConversations";
import { useLiveOutputs } from "./liveOutput";

export type OutputTarget = Pick<OutputActivityOpen, "artifactId" | "conversationId" | "lineageId" | "typeId">;
export type OutputTrashTarget = { id: string; lineageId: string } | { id: string; conversation: true };
type Turn = Omit<ArtifactTurnInput, "clientRequestId">;
type Listen = (hear: (event: ArtifactTurnEvent) => void, signal: AbortSignal) => Promise<OutputDone | null>;
type Failure = { message: string; build?: BuildTurnInput | undefined; createdAt: string };

export function latestVersions(versions: ArtifactSummary[]): ArtifactSummary[] {
  const heads = new Map<string, ArtifactSummary>();
  for (const version of versions) {
    if ((heads.get(version.lineageId)?.versionNumber ?? -1) < version.versionNumber) heads.set(version.lineageId, version);
  }
  return [...heads.values()];
}

export function useStudio(baseId: string, workspaceId: string | null) {
  const { items } = useActivity();
  const activities = items.filter((item) => item.open?.view === "library" && item.open.tab === "outputs" && item.open.libraryId === baseId);
  const [types, setTypes] = useState<OutputType[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [artifactsLoaded, setArtifactsLoaded] = useState(false);
  const [conversations, setConversations] = useState<ArtifactConversationSummary[]>([]);
  const [details, setDetails] = useState<Record<string, ArtifactConversationDetail>>({});
  const [failures, setFailures] = useState<Record<string, Failure | undefined>>({});
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const { jobs, watch, stop, reset } = useLiveOutputs();
  const loadOrder = useRef(0);
  const remember = useCallback((detail: ArtifactConversationDetail) => {
    setDetails((current) => (current[detail.id]?.updatedAt ?? "") > detail.updatedAt ? current : { ...current, [detail.id]: detail });
    setConversations((current) => current.some((item) => item.id === detail.id && item.updatedAt > detail.updatedAt)
      ? current : [detail, ...current.filter((item) => item.id !== detail.id)]);
    return detail;
  }, []);
  const rememberVersion = useCallback((artifact: Artifact) => {
    loadOrder.current += 1;
    setArtifacts((current) => [artifact, ...current.filter((item) => item.id !== artifact.id)]);
  }, []);
  const forgetTrashed = (target: OutputTrashTarget) => {
    if ("conversation" in target) {
      setArtifacts((current) => current.filter((item) => item.conversationId !== target.id));
      setConversations((current) => current.filter((item) => item.id !== target.id));
      setFailures((current) => ({ ...current, [target.id]: undefined }));
    } else {
      setArtifacts((current) => current.filter((item) => item.lineageId !== target.lineageId));
    }
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
      const list = await knowledgeApi.artifactConversations(baseId, workspaceId);
      if (order === loadOrder.current) { setConversations(list); setError(importError ?? ""); }
    } catch (reason) {
      if (order === loadOrder.current) setError(reason instanceof Error ? reason.message : "Could not load outputs.");
    }
  }, [baseId, workspaceId]);

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
    setArtifacts([]); setArtifactsLoaded(false); setConversations([]); setDetails({}); setFailures({});
    void reload();
    return () => { loadOrder.current += 1; reset(); };
  }, [reload, reset]);
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener("gunther:trash-updated", refresh);
    return () => window.removeEventListener("gunther:trash-updated", refresh);
  }, [reload]);
  const finished = activities.filter((item) => item.state === "done" || item.state === "failed")
    .map((item) => [item.id, item.endedAt, item.open, item.state]);
  const finishedKey = finished.length ? JSON.stringify(finished) : "";
  useEffect(() => { if (finishedKey) void reload(); }, [finishedKey, reload]);

  const read = useCallback(async (id: string) => {
    if (!workspaceId) return null;
    try { return remember(await knowledgeApi.artifactConversation(baseId, id, workspaceId)); }
    catch (reason) {
      if (reason instanceof ApiRequestError && reason.status === 404) return null;
      throw reason;
    }
  }, [baseId, workspaceId, remember]);
  const turnStop = useCallback((id: string) => {
    if (!workspaceId) return Promise.resolve();
    return knowledgeApi.stopArtifactTurn(baseId, id, workspaceId);
  }, [baseId, workspaceId]);

  const run = useCallback(async (id: string, info: Parameters<typeof watch>[1], listen: Listen) => {
    const listener = watch(id, info);
    if (!listener) return null;
    if (info.kind !== "follow") setFailures((current) => ({ ...current, [id]: undefined }));
    try {
      const done = await listen(listener.hear, listener.signal);
      if (listener.signal.aborted || !done) return null;
      const artifact = isTurnResult(done) ? done.artifact : done;
      if (artifact) rememberVersion(artifact);
      await Promise.allSettled([read(id), reload()]);
      return done;
    } catch (reason) {
      if (listener.signal.aborted) return null;
      if (!(reason instanceof OutputStoppedError)) {
        const failure = { message: reason instanceof Error ? reason.message : "The request could not be completed.", build: info.build, createdAt: new Date().toISOString() };
        setFailures((current) => ({ ...current, [id]: failure }));
      }
      await Promise.allSettled([read(id), reload()]);
      return null;
    } finally {
      listener.finish();
    }
  }, [watch, rememberVersion, read, reload]);

  const follow = useCallback((id: string) => {
    if (!workspaceId) return;
    void run(id, { kind: "follow", stop: () => turnStop(id) },
      (hear, signal) => knowledgeApi.followArtifactTurn(baseId, id, workspaceId, hear, signal));
  }, [baseId, workspaceId, run, turnStop]);

  const ensure = async (id: string, scope: OutputScope) => {
    if (!workspaceId || conversations.some((item) => item.id === id)) return;
    remember(await knowledgeApi.createArtifactConversation(baseId, { id, scope }, workspaceId));
  };
  const send = (id: string, turn: Turn, scope?: OutputScope) => {
    if (!workspaceId) return Promise.resolve(null);
    const turnId = artifactRequestId();
    return run(id, { kind: "turn", lineageId: turn.targetLineageId, build: turn.build, stop: () => turnStop(id) }, async (hear, signal) => {
      if (scope) await ensure(id, scope);
      if (signal.aborted) return null;
      return knowledgeApi.artifactTurnStream(baseId, id, { ...turn, clientRequestId: turnId }, workspaceId, hear, signal);
    });
  };
  const build = (id: string, options: BuildTurnInput) => send(id, { kind: "build", build: options }, options.scope);
  const retry = async (id: string, options: BuildTurnInput) => {
    const conversation = await read(id).catch(() => details[id]);
    const asked = conversation && [...conversation.messages].reverse().find((item) => item.role === "user" && item.kind === "build");
    const lineage = asked?.payload.targetLineageId;
    return send(id, { kind: "build", build: options, ...(typeof lineage === "string" ? { targetLineageId: lineage } : {}) }, options.scope);
  };
  const direct = (id: string, artifact: Artifact, title: string, listen: Listen) =>
    run(id, { kind: "version", lineageId: artifact.lineageId, title, stop: () => artifact.conversationId
      ? turnStop(id) : knowledgeApi.stopActivity(`output-lineage:${artifact.lineageId}`) }, listen);

  const owner = (artifact: ArtifactSummary) => artifact.conversationId
    || (conversations.some((item) => item.id === EARLIER_WORK) ? EARLIER_WORK : artifact.lineageId);

  return { types, artifacts, artifactsLoaded, conversations, details, activities, jobs, failures, error: catalogError || error, remember, rememberVersion, forgetTrashed,
    read, follow, ensure, send, build, retry, direct, owner, stop };
}
