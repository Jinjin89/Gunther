import type { CreateSourceInput } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { removeStoredCapture } from "../localCaptureQueue";
import type { CaptureKind } from "./captureTypes";

export interface TextCaptureInput {
  title: string;
  baseId: string | null;
  content: string;
  kind: CaptureKind;
  captureId: string;
  url?: string | undefined;
  workspaceId?: string | null | undefined;
}

export interface AssetCaptureInput {
  file: File;
  title: string;
  baseId: string | null;
  kind: "file" | "image";
  notes: string;
  workspaceId?: string | null | undefined;
}

export interface CapturePersistenceResult {
  message: string;
  savedToService: boolean;
}

export async function persistTextCapture(input: TextCaptureInput): Promise<CapturePersistenceResult> {
  const { title, baseId, content, kind, captureId, url, workspaceId } = input;
  let savedToService = false;
  let filedToRequestedBase = !baseId;

  try {
    if (!workspaceId) throw new Error("The local workspace identity is not available yet.");
    let noteId: string | undefined;
    if (kind === "note") {
      const note = await knowledgeApi.createNote({
        title: title || "Untitled note",
        content,
        clientCaptureId: captureId,
      }, workspaceId);
      noteId = note.id;
    } else if (kind === "link" && url) {
      await knowledgeApi.captureWeb({
        url,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(content.trim() ? { notes: content.trim() } : {}),
        ...(baseId ? { knowledgeBaseId: baseId } : {}),
        clientCaptureId: captureId,
      }, workspaceId);
    } else {
      // Older queued links without a URL already contain their readable text.
      await knowledgeApi.importSource({
        title: title || "Untitled source",
        content,
        kind: kind as CreateSourceInput["kind"],
        ...(baseId ? { knowledgeBaseId: baseId } : {}),
      }, workspaceId, { deferAnalysis: true });
    }
    removeStoredCapture(captureId);
    savedToService = true;
    if (noteId && baseId) {
      try {
        await knowledgeApi.fileNote(noteId, baseId, workspaceId);
        filedToRequestedBase = true;
      } catch {
        // The editable Inbox note remains durable even if filing must be retried.
      }
    }
  } catch {
    // CaptureSheet writes the durable retry copy before this function runs.
  }

  const message = savedToService
    ? kind === "note" && (!baseId || !filedToRequestedBase)
      ? filedToRequestedBase
        ? "Editable note preserved in Inbox. File it when its subject becomes clear."
        : "Editable note is safe in Inbox; its requested library was unavailable."
      : kind === "link" && url
        ? baseId
          ? "Immutable page snapshot preserved with its source provenance."
          : "Immutable page snapshot preserved in Inbox. Organize it whenever you are ready."
        : baseId
          ? "Source preserved. Review suggestions whenever you are ready."
          : "Source preserved in Inbox. Organize it whenever you are ready."
    : `“${title}” is preserved in the local retry queue until the knowledge service reconnects.`;

  return { message, savedToService };
}

export async function persistAssetCapture(input: AssetCaptureInput): Promise<CapturePersistenceResult> {
  const { file, title, baseId, kind, notes, workspaceId } = input;
  if (!workspaceId) throw new Error("The local workspace identity is not available yet.");
  await knowledgeApi.captureAsset(file, title, kind, baseId, notes, workspaceId);
  return {
    savedToService: true,
    message: baseId
      ? "Original file preserved. Review its extracted suggestions when ready."
      : "Original file preserved in Inbox. Organize it whenever you are ready.",
  };
}
