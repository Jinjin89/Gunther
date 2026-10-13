import { useEffect, useState } from "react";
import { persistentStorage } from "../persistentStorage";

export function useSidebarLayout(workingOnArtifacts: boolean, focused: boolean) {
  const [preference, setPreference] = useState<"auto" | "expanded" | "collapsed">(() => {
    try { const value = persistentStorage.getItem("gunther:sidebar-layout"); return value === "expanded" || value === "collapsed" ? value : "auto"; } catch { return "auto"; }
  });
  const [narrow, setNarrow] = useState(() => window.innerWidth < 1100);
  useEffect(() => { const resize = () => setNarrow(window.innerWidth < 1100); window.addEventListener("resize", resize); return () => window.removeEventListener("resize", resize); }, []);
  useEffect(() => { try { persistentStorage.setItem("gunther:sidebar-layout", preference); } catch { /* Current layout does not depend on storage. */ } }, [preference]);
  const collapsed = focused || (preference === "auto" ? workingOnArtifacts || narrow : preference === "collapsed");
  return { collapsed, automatic: preference === "auto", toggle: () => setPreference(collapsed ? "expanded" : "collapsed"), reset: () => setPreference("auto") };
}
