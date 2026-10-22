import type { Artifact } from "@gunther/contracts";
import { sourceAssetUrl } from "../../api";

/** Save a file the way the app's downloads do: a link with `download`, which the desktop shell turns into a save dialog. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

const fileBase = (artifact: Artifact) => (artifact.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 60) || artifact.kind) + `-v${artifact.versionNumber}`;
const sourcesOf = (artifact: Artifact) => artifact.citations.map((citation) => [citation.sourceTitle, citation.locator].filter(Boolean).join(" · "));

async function imageData(assetId: string): Promise<string | null> {
  try {
    const response = await fetch(sourceAssetUrl(assetId));
    if (!response.ok) return null;
    const blob = await response.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).replace(/^data:/, ""));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

/** The deck as a PowerPoint file, saved. Returns the file's name. */
export async function downloadPptx(artifact: Artifact): Promise<string> {
  const { pptxBytes } = await import("./exportPptx");
  const bytes = await pptxBytes(artifact.content, { sources: sourcesOf(artifact), resolveImage: imageData });
  const name = `${fileBase(artifact)}.pptx`;
  saveBlob(new Blob([bytes as BlobPart], { type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }), name);
  return name;
}

/** The report as a Word file, saved. Returns the file's name. */
export async function downloadDocx(artifact: Artifact): Promise<string> {
  const { docxBlob } = await import("./exportDocx");
  const name = `${fileBase(artifact)}.docx`;
  saveBlob(await docxBlob(artifact.content, { sources: sourcesOf(artifact), title: artifact.title }), name);
  return name;
}
