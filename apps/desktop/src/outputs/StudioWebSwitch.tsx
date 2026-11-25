import { WEB_SEARCH_HINT } from "../models/askModel";

export function StudioWebSwitch({ available, checked, onChange }: {
  available: boolean; checked: boolean; onChange: (checked: boolean) => void;
}) {
  const hint = available ? undefined : WEB_SEARCH_HINT;
  return <label className="studio-web-switch" title={hint}>
    <input type="checkbox" role="switch" checked={available && checked} disabled={!available}
      title={hint} onChange={(event) => onChange(event.currentTarget.checked)} />
    Search the web
  </label>;
}
