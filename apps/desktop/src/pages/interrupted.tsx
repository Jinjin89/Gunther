import type { SessionMessage } from "@gunther/contracts";
import { CircleAlert, CircleSlash, RotateCcw, SquarePen } from "lucide-react";
import { useState } from "react";
import { AnswerStreamError } from "../api";
import { openSettings } from "../components/SettingsLayout";
import { AnswerTrace } from "../developer/AnswerTrace";

type Reason = "stopped" | "failed";

/** A question that got no answer: kept in the conversation, marked, and easy to ask again. */
export const isInterrupted = (message: SessionMessage) => message.role === "user" && Boolean(message.context.interrupted);

/** The marker shown at once when a question is stopped; the service's saved copy replaces it. */
export function interruptedQuestion(sessionId: string, content: string, reason: Reason): SessionMessage {
  return {
    id: `local-interrupted-${Date.now()}`,
    sessionId,
    role: "user",
    content,
    citations: [],
    context: { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "local", interrupted: reason },
    createdAt: new Date().toISOString(),
  };
}

/** Whether this attempt's failed question is in the reloaded conversation. */
export const hasSavedQuestion = (messages: SessionMessage[] | undefined, error: unknown) =>
  error instanceof AnswerStreamError && messages?.some((message) => message.id === error.questionId && isInterrupted(message)) === true;

export function InterruptedNote({ message, onRetry, onEdit, disabled }: { message: SessionMessage; onRetry: () => void; onEdit: () => void; disabled?: boolean }) {
  const { interrupted: reason, modelError, askFailure } = message.context;
  const [showTrace, setShowTrace] = useState(false);
  const Icon = reason === "stopped" ? CircleSlash : CircleAlert;
  return <><div className="gx-interrupted" role="note">
    <span className="gx-interrupted-label"><Icon size={13} />{modelError ?? (reason === "stopped" ? "Stopped before Gunther answered" : "Gunther could not finish this answer")}</span>
    <span className="gx-interrupted-actions">
      {askFailure?.needsModel && <button type="button" onClick={() => openSettings("models")}>Open Settings</button>}
      <button type="button" onClick={onRetry} disabled={disabled}><RotateCcw size={12} />{reason === "stopped" ? "Ask again" : "Retry"}</button>
      <button type="button" onClick={onEdit} disabled={disabled}><SquarePen size={12} />Edit</button>
      {askFailure?.hasTrace && <button type="button" onClick={() => setShowTrace(!showTrace)} aria-expanded={showTrace}>Trace</button>}
    </span>
  </div>{showTrace && <AnswerTrace sessionId={message.sessionId} messageId={message.id} onCopy={(_, value) => void navigator.clipboard?.writeText(value).catch(() => undefined)} />}</>;
}
