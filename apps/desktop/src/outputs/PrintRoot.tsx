import type { Artifact } from "@gunther/contracts";
import { createPortal } from "react-dom";
import { outputViews } from "./OutputViews";

const DOCUMENT_PAGE = "@page { size: A4; margin: 18mm 16mm; }";

/** The open version laid out for paper, hidden on screen. */
export function PrintRoot({ artifact, from = 0, to = Number.POSITIVE_INFINITY }: { artifact: Artifact; from?: number; to?: number }) {
  const { Print, pageRule } = outputViews[artifact.kind];
  return createPortal(
    <div id="print-root" aria-hidden="true" data-kind={artifact.kind}>
      <style>{pageRule ?? DOCUMENT_PAGE}</style>
      <Print artifact={artifact} from={from} to={to} />
    </div>,
    document.body,
  );
}
