import type { Assertion } from "@gunther/contracts";
import { ArrowRight, CheckCircle2, CircleDashed, ShieldAlert } from "lucide-react";

const statusIcon = {
  provisional: CircleDashed,
  verified: CheckCircle2,
  disputed: ShieldAlert,
};

export function AssertionRow({ assertion, compact = false }: { assertion: Assertion; compact?: boolean }) {
  const StatusIcon = statusIcon[assertion.status];
  return (
    <article className={`assertion-row ${compact ? "is-compact" : ""}`}>
      <div className="assertion-main">
        <span className="entity-token">{assertion.subject.label}</span>
        <span className="predicate-token">
          <ArrowRight size={14} aria-hidden="true" />
          {assertion.predicate.replaceAll("_", " ")}
        </span>
        <span className="entity-token">{assertion.object.label}</span>
      </div>
      <div className="assertion-meta">
        <span className={`status-label status-${assertion.status}`}>
          <StatusIcon size={14} aria-hidden="true" />
          {assertion.status}
        </span>
        <span>{assertion.source.title}</span>
        <span>{Math.round(assertion.confidence * 100)}% confidence</span>
      </div>
    </article>
  );
}
