import { ChevronDown, Layers3, Plus } from "lucide-react";
import { domains, type DomainId } from "../prototype";

interface DomainLensBarProps {
  active: DomainId;
  onChange: (domain: DomainId) => void;
  onCapture: () => void;
}

export function DomainLensBar({ active, onChange, onCapture }: DomainLensBarProps) {
  return (
    <div className="domain-lensbar">
      <div className="library-identity">
        <span className="library-mark"><Layers3 size={15} /></span>
        <span><strong>Living library</strong><small>24 knowledge units · 64 claims</small></span>
        <ChevronDown size={13} />
      </div>
      <div className="domain-lenses" role="tablist" aria-label="Knowledge domains">
        {domains.map((domain) => (
          <button
            key={domain.id}
            className={active === domain.id ? "is-active" : ""}
            onClick={() => onChange(domain.id)}
            role="tab"
            aria-selected={active === domain.id}
            title={domain.detail}
          >
            <i className={`domain-dot domain-${domain.id}`} />
            {domain.label}
            <span>{domain.count}</span>
          </button>
        ))}
      </div>
      <button className="lens-capture" onClick={onCapture}><Plus size={14} />Capture</button>
    </div>
  );
}
