import type { Artifact, OutputType } from "@gunther/contracts";
import { createPortal } from "react-dom";
import { PrintCodeContext } from "../components/markdown/rich/CodeBlock";
import { outputViews } from "./OutputViews";

const DOCUMENT_PAGE = "@page { size: A4; margin: 18mm 16mm; }";

/** The open version laid out for paper, hidden on screen. */
export function PrintRoot({ artifact, type, from = 0, to = Number.POSITIVE_INFINITY }: {
  artifact: Artifact; type?: OutputType | undefined; from?: number; to?: number;
}) {
  const { Print, pageRule } = outputViews[artifact.kind];
  return createPortal(
    <div id="print-root" aria-hidden="true" data-kind={artifact.kind} data-artifact-id={artifact.id}>
      <style>{pageRule ?? DOCUMENT_PAGE}</style>
      <PrintCodeContext.Provider value={true}><Print artifact={artifact} type={type} presentation="print" from={from} to={to} /></PrintCodeContext.Provider>
    </div>,
    document.body,
  );
}
