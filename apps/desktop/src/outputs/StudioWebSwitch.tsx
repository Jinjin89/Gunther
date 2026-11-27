import { useId } from "react";
import { WEB_SEARCH_HINT } from "../models/askModel";

export function StudioWebSwitch({ available, checked, onChange, disabled = false, showHint = false, allowTurnOff = false }: {
  available: boolean; checked: boolean; onChange: (checked: boolean) => void;
  disabled?: boolean; showHint?: boolean; allowTurnOff?: boolean;
}) {
  const id = useId();
  const hint = available ? undefined : WEB_SEARCH_HINT;
  const enabled = available || (allowTurnOff && checked);
  return <>
    <label className="studio-web-switch" title={hint}>
      <input type="checkbox" role="switch" checked={enabled && checked} disabled={disabled || !enabled}
        aria-describedby={hint && showHint ? id : undefined}
        title={hint} onChange={(event) => onChange(event.currentTarget.checked)} />
      Search the web
    </label>
    {hint && showHint && <small className="plan-hint" id={id}>{hint}</small>}
  </>;
}
