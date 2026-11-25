import { ArrowUpRight } from "lucide-react";
import { webFindKey, type WebFind, type WebFindImports } from "../../pages/useImportWebFind";

export function WebFindActions({ find, imports, outsideLabel }: {
  find: WebFind;
  imports?: WebFindImports | undefined;
  outsideLabel?: string;
}) {
  const state = imports?.status(find);
  const imported = state?.status === "imported";
  const link = find.url && webFindKey(find.url) ? find.url : null;
  return <div className="gx-web-find-actions">
    <div className="gx-web-find-buttons">
      {imported ? <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => imports?.openSource(state.sourceId)}>
        <span aria-hidden="true">✓</span> In library · {outsideLabel ? "Open source" : "Open"}
      </button> : imports && link && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
        disabled={state?.status === "importing" || imports.batching} onClick={() => void imports.importOne(find)}>
        {state?.status === "importing" ? "Adding…" : state?.status === "error" ? "Retry" : "+ Add to library"}
      </button>}
      {link && (!imported || outsideLabel) && <a className={`gx-btn gx-btn-sm ${outsideLabel && !imports ? "gx-btn-primary" : "gx-btn-quiet"}`}
        href={link} target="_blank" rel="noreferrer noopener">{outsideLabel ?? "Open"}<ArrowUpRight size={13} aria-hidden="true" /></a>}
    </div>
    {state?.status === "error" && <p role="alert" className="gx-web-find-error">{state.message}</p>}
  </div>;
}
