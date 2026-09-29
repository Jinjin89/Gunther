import type { SessionMessage } from "@gunther/contracts";
import { CircleAlert, CircleSlash, RotateCcw, SquarePen } from "lucide-react";

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

/** Whether the saved conversation already holds this unanswered question last. */
export const endsWithInterrupted = (messages: SessionMessage[], content: string) => {
  const last = messages[messages.length - 1];
  return Boolean(last && isInterrupted(last) && last.content === content.trim());
};

export function InterruptedNote({ reason, onRetry, onEdit, disabled, className = "" }: { reason: Reason; onRetry: () => void; onEdit: () => void; disabled?: boolean; className?: string }) {
  const Icon = reason === "stopped" ? CircleSlash : CircleAlert;
  return <div className={`gx-interrupted ${className}`} role="note">
    <span className="gx-interrupted-label"><Icon size={13} />{reason === "stopped" ? "Stopped before Gunther answered" : "Gunther could not finish this answer"}</span>
    <span className="gx-interrupted-actions">
      <button type="button" onClick={onRetry} disabled={disabled}><RotateCcw size={12} />Ask again</button>
      <button type="button" onClick={onEdit} disabled={disabled}><SquarePen size={12} />Edit</button>
    </span>
  </div>;
}
