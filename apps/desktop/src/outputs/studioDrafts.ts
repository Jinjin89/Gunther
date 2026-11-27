import { outputTypeIds } from "@gunther/contracts";
import { useCallback, useReducer, useState } from "react";
import { persistentStorage } from "../persistentStorage";
import { validScope } from "./artifactConversations";
import type { LocalSetup, PlanAddress } from "./useStudio";

const prefixFor = (baseId: string, workspaceId: string | null) => workspaceId
  ? `gunther:studio-setup:${encodeURIComponent(workspaceId)}:${encodeURIComponent(baseId)}:`
  : null;
const keyFor = (prefix: string, address: PlanAddress) =>
  `${prefix}${encodeURIComponent(address.conversationId)}:${encodeURIComponent(address.planId)}`;

type Drafts = Record<string, LocalSetup | undefined>;

function readDrafts(prefix: string | null): Drafts {
  const drafts: Drafts = {};
  if (!prefix) return drafts;
  try {
    for (let index = 0; index < persistentStorage.length; index++) {
      const key = persistentStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      try {
        const draft = JSON.parse(persistentStorage.getItem(key) ?? "null") as LocalSetup | null;
        if (draft?.touched === true &&
          typeof draft.conversationId === "string" && typeof draft.planId === "string" && draft.planId.startsWith("draft_") &&
          key === keyFor(prefix, draft) &&
          typeof draft.firstRequestId === "string" && typeof draft.updatedAt === "string" &&
          draft.setup && outputTypeIds.includes(draft.setup.type) && validScope(draft.setup.scope)) {
          drafts[draft.planId] = draft;
        }
      } catch { /* One unreadable entry does not hide other drafts. */ }
    }
  } catch { /* Setup stays available in memory if device storage is unavailable. */ }
  return drafts;
}

export function useStudioDrafts(baseId: string, workspaceId: string | null) {
  const prefix = prefixFor(baseId, workspaceId);
  const [saved, setSaved] = useState(() => ({ prefix, locals: readDrafts(prefix) }));
  const store = saved.prefix === prefix ? saved : { prefix, locals: readDrafts(prefix) };
  if (store !== saved) setSaved(store);
  const [, redraw] = useReducer((revision: number) => revision + 1, 0);

  // Update immediately so two clicks in a single render see the same draft.
  const setLocal = useCallback((draft: LocalSetup) => {
    const previous = store.locals[draft.planId];
    if (previous === draft) return;
    const next = previous?.touched && !draft.touched ? { ...draft, touched: true } : draft;
    store.locals = { ...store.locals, [next.planId]: next };
    if (store.prefix && next.touched) {
      try { persistentStorage.setItem(keyFor(store.prefix, next), JSON.stringify(next)); }
      catch { /* The open form still has the draft. */ }
    }
    redraw();
  }, [store]);
  const discardLocal = useCallback((planId: string) => {
    const draft = store.locals[planId];
    if (!draft) return;
    const next = { ...store.locals };
    delete next[planId];
    store.locals = next;
    if (store.prefix && draft.touched) {
      try { persistentStorage.removeItem(keyFor(store.prefix, draft)); }
      catch { /* The in-memory draft was removed. */ }
    }
    redraw();
  }, [store]);
  return { store, setLocal, discardLocal };
}
