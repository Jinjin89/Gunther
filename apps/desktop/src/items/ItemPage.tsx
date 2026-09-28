import type { KnowledgeProposal, NotebookNote, SourceDetail } from "@gunther/contracts";
import { CircleAlert, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { useEscape, useShortcut } from "../shortcuts/shortcuts";
import { ItemLayout, ItemSkeleton, type ItemNavigation } from "./ItemLayout";
import { itemKey, type ItemRef } from "./itemRef";
import { NoteItem } from "./NoteItem";
import { SourceItem } from "./SourceItem";
import { SuggestionItem } from "./SuggestionItem";

type Loaded =
  | { key: string; type: "source"; source: SourceDetail }
  | { key: string; type: "note"; note: NotebookNote }
  | { key: string; type: "suggestion"; proposal: KnowledgeProposal };

export interface ItemPageProps {
  item: ItemRef;
  bases: KnowledgeBase[];
  backLabel: string;
  position: { index: number; total: number } | null;
  onBack: () => void;
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  /** A decision was made; the host shows `message` and moves on. */
  onResolved: (message: string) => void;
  onOpenBase: (id: string) => void;
  onOpenSession: (baseId: string, sessionId: string, messageId: string | null) => void;
  onOpenNotebook: (noteId: string) => void;
  onCreateBase: () => void;
  onNotify: (message: string) => void;
  onTitle: (title: string) => void;
}

async function loadItem(item: ItemRef): Promise<Loaded> {
  const key = itemKey(item);
  if (item.type === "source") return { key, type: "source", source: await knowledgeApi.source(item.id) };
  if (item.type === "note") {
    const note = (await knowledgeApi.notes()).find((candidate) => candidate.id === item.id);
    if (!note) throw new Error("This note no longer exists.");
    return { key, type: "note", note };
  }
  const baseIds = item.baseId ? [item.baseId] : (await knowledgeApi.knowledgeBases()).map((base) => base.id);
  for (const baseId of baseIds) {
    const proposal = (await knowledgeApi.proposals(baseId)).find((candidate) => candidate.id === item.id);
    if (proposal) return { key, type: "suggestion", proposal };
  }
  throw new Error("This suggestion no longer exists.");
}

export function ItemPage(props: ItemPageProps) {
  const { item, onBack, onNext, onPrevious } = props;
  const key = itemKey(item);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void loadItem(item).then((next) => {
      if (active) setLoaded(next);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "This item could not be opened.");
    });
    return () => { active = false; };
    // `key` identifies the item; the object itself may be recreated by the host.
  }, [attempt, key]);

  useEscape(onBack, true, "blur");
  useShortcut("mod+[", onBack);
  useShortcut("j", () => onNext?.(), { enabled: Boolean(onNext) });
  useShortcut("k", () => onPrevious?.(), { enabled: Boolean(onPrevious) });

  const nav: ItemNavigation = { backLabel: props.backLabel, onBack, position: props.position, onPrevious, onNext };
  const current = loaded?.key === key ? loaded : null;

  if (error && !current) {
    return (
      <ItemLayout nav={nav} header={<header className="gx-item-header"><h1 className="gx-item-title">Couldn’t open this item</h1></header>}>
        <div className="gx-banner is-error" role="alert">
          <CircleAlert size={16} />
          <span><strong>{error}</strong><small>Your captures are safe. It may have been filed, archived or removed elsewhere.</small></span>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setAttempt((value) => value + 1)}><RotateCcw size={13} />Retry</button>
        </div>
      </ItemLayout>
    );
  }
  if (!current) return <ItemSkeleton nav={nav} />;

  const shared = { bases: props.bases, nav, onResolved: props.onResolved, onOpenBase: props.onOpenBase, onNotify: props.onNotify, onTitle: props.onTitle };
  if (current.type === "note") return <NoteItem key={current.key} note={current.note} {...shared} onOpenNotebook={props.onOpenNotebook} onCreateBase={props.onCreateBase} />;
  if (current.type === "suggestion") return <SuggestionItem key={current.key} proposal={current.proposal} {...shared} onOpenSession={props.onOpenSession} />;
  return <SourceItem key={current.key} source={current.source} {...shared} onCreateBase={props.onCreateBase} />;
}
