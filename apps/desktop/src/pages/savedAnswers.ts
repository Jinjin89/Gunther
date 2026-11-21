import type { KnowledgeProposal, KnowledgeSession, KnowledgeUnit, SessionMessage } from "@gunther/contracts";
import { useCallback, useEffect, useState } from "react";
import { knowledgeApi } from "../api";

export interface SavedAnswer {
  id: string;
  status: KnowledgeProposal["status"];
}

const trusted = (units: KnowledgeUnit[]) => units.filter((unit) => unit.status === "trusted");

/**
 * Answers saved as knowledge in one library. Saving is the only review step: the answer
 * becomes a trusted unit at once, and Remove takes it back out (it can be saved again).
 */
export function useSavedAnswers(baseId: string, onNotify: (message: string) => void, onError: (message: string | null) => void, { loadOnMount = true } = {}) {
  const [saved, setSaved] = useState<Record<string, SavedAnswer>>({});
  const [units, setUnits] = useState<KnowledgeUnit[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reloadProposals = useCallback(async (signal?: AbortSignal) => {
    const proposals = await knowledgeApi.proposals(baseId);
    if (signal?.aborted) return;
    setSaved(Object.fromEntries(proposals.map((item) => [item.messageId, { id: item.id, status: item.status }])));
  }, [baseId]);

  const reloadUnits = useCallback(async (signal?: AbortSignal) => {
    const all = await knowledgeApi.knowledgeUnits(baseId);
    if (!signal?.aborted) setUnits(trusted(all));
  }, [baseId]);

  useEffect(() => {
    if (!loadOnMount) return undefined;
    const controller = new AbortController();
    void Promise.allSettled([reloadProposals(controller.signal), reloadUnits(controller.signal)]);
    return () => controller.abort();
  }, [loadOnMount, reloadProposals, reloadUnits]);

  const decide = async (message: SessionMessage, run: () => Promise<KnowledgeProposal>, done: string, failed: string) => {
    setBusyId(message.id);
    onError(null);
    try {
      const proposal = await run();
      setSaved((current) => ({ ...current, [message.id]: { id: proposal.id, status: proposal.status } }));
      onNotify(done);
      void reloadUnits().catch(() => undefined);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : failed);
    } finally {
      setBusyId(null);
    }
  };

  return {
    reloadProposals,
    reloadUnits,
    /** Trusted units only; a removed answer's unit is not listed. */
    units,
    /** The message being saved or removed, if any. */
    busyId,
    isSaved: (messageId: string) => saved[messageId]?.status === "accepted",
    save: async (session: Pick<KnowledgeSession, "id" | "focusChapterId"> | null, message: SessionMessage) => {
      if (!session) {
        onNotify("Reconnect the knowledge service to save this answer.");
        return;
      }
      await decide(message, () => knowledgeApi.createProposal(session.id, message.id, { targetChapterId: session.focusChapterId }), "Saved as knowledge.", "Could not save this answer");
    },
    remove: async (message: SessionMessage) => {
      const answer = saved[message.id];
      if (!answer) return;
      await decide(message, () => knowledgeApi.updateProposal(answer.id, { status: "rejected", reason: "Removed from knowledge" }), "Removed from knowledge.", "Could not remove this answer");
    },
  };
}
