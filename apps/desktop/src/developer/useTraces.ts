import { useCallback, useEffect, useState } from "react";
import { knowledgeApi } from "../api";

const CHANGED = "gunther:developer-changed";

/** Whether answers keep how they were made (Settings → Developer), and a way to change it. */
export function useTraces() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let active = true;
    const load = () => void knowledgeApi.developerSettings().then((settings) => { if (active) setOn(settings.traces); }).catch(() => undefined);
    load();
    window.addEventListener(CHANGED, load);
    return () => { active = false; window.removeEventListener(CHANGED, load); };
  }, []);
  const change = useCallback(async (traces: boolean) => {
    const saved = await knowledgeApi.saveDeveloperSettings({ traces });
    setOn(saved.traces);
    window.dispatchEvent(new CustomEvent(CHANGED));
    return saved.traces;
  }, []);
  return { on, change };
}
