import type { ModelProvider, ServiceCheck, ServiceState } from "@gunther/contracts";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";

const STATE_LABELS: Record<ServiceState, string> = {
  configured: "Set up",
  not_configured: "Not set up",
  error: "Needs attention",
  off: "Off",
};

export function ServiceStatusBadge({ status }: { status: { state: ServiceState; check: ServiceCheck | null } }) {
  const { state, check } = status;
  const label = state === "configured" && check?.ok ? "Connected" : STATE_LABELS[state];
  return <span className={`service-badge is-${state}`}><i aria-hidden="true" />{label}</span>;
}

interface SecretFieldProps {
  inputId: string;
  label: string;
  describedBy?: string;
  placeholder: string;
  isSet: boolean;
  hint: string | null;
  /** undefined keeps the current key; an empty string removes it. */
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  /** Autosaving forms commit a finished replacement or an explicit removal. */
  onCommit?: (value: string) => void;
}

export function SecretField({
  inputId, label, describedBy, placeholder, isSet, hint, value, onChange, onCommit,
}: SecretFieldProps) {
  const [visible, setVisible] = useState(false);
  const [replacing, setReplacing] = useState(false);
  useEffect(() => setReplacing(false), [hint, isSet]);

  if (isSet && !replacing && !value) {
    const removing = value === "";
    return <div className="service-secret-saved">
      <span className={`service-secret-mask ${removing ? "is-removing" : ""}`}>
        <KeyRound size={13} aria-hidden="true" />
        {removing ? "Removed when you save" : `•••• ${hint ?? "saved"}`}
      </span>
      {removing
        ? <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onChange(undefined)}>Undo</button>
        : <>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setReplacing(true)}>Replace</button>
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { onChange(""); onCommit?.(""); }}>Remove</button>
        </>}
    </div>;
  }

  return <div className="service-secret-input">
    <input
      id={inputId}
      type={visible ? "text" : "password"}
      value={value ?? ""}
      placeholder={placeholder}
      autoComplete="off"
      spellCheck={false}
      autoFocus={replacing}
      aria-describedby={describedBy}
      onChange={(event) => onChange(event.target.value || undefined)}
      onBlur={() => { if (value) onCommit?.(value); }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && value && onCommit) {
          event.preventDefault();
          onCommit(value);
        }
      }}
    />
    <button type="button" className="gx-icon-button" aria-label={visible ? `Hide ${label}` : `Show ${label}`} title={visible ? "Hide" : "Show"} onClick={() => setVisible((current) => !current)}>
      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
    </button>
    {replacing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setReplacing(false); onChange(undefined); }}>Cancel</button>}
  </div>;
}

type KeyedProvider = Pick<ModelProvider, "name" | "keySet" | "keyHint" | "keyOptional"> & {
  keySource?: ModelProvider["keySource"];
  keyShared?: boolean;
};

export function ProviderKeyField({ provider, ...input }: Omit<SecretFieldProps, "label" | "isSet" | "hint" | "placeholder"> & {
  provider: KeyedProvider;
}) {
  const placeholder = provider.keyShared ? `Using your ${provider.name} key from Models`
    : provider.keyOptional ? "Only if your server asks for one" : "Paste your key";
  return <SecretField {...input} label="API key" isSet={provider.keySet && provider.keySource !== "none"} hint={provider.keyHint} placeholder={placeholder} />;
}
