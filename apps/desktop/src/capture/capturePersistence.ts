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
    if (kind === "note") {
      const note = await knowledgeApi.createNote({
        title: title || "Untitled note",
        content,
        clientCaptureId: captureId,
      }, workspaceId);
      removeStoredCapture(captureId);
      savedToService = true;
      if (baseId) {
        try {
          await knowledgeApi.fileNote(note.id, baseId, workspaceId);
          filedToRequestedBase = true;
        } catch {
          // The editable Inbox note remains durable even if filing must be retried.
        }
      }
    } else if (kind === "link") {
      if (!url) throw new Error("A valid public web page URL is required.");
      await knowledgeApi.captureWeb({
        url,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(content.trim() ? { notes: content.trim() } : {}),
        ...(baseId ? { knowledgeBaseId: baseId } : {}),
        clientCaptureId: captureId,
      }, workspaceId);
      removeStoredCapture(captureId);
      savedToService = true;
      filedToRequestedBase = true;
    } else {
      await knowledgeApi.importSource({
        title,
        content,
        kind: kind as CreateSourceInput["kind"],
        ...(baseId ? { knowledgeBaseId: baseId } : {}),
      }, workspaceId);
      removeStoredCapture(captureId);
      savedToService = true;
      filedToRequestedBase = true;
    }
  } catch {
    // CaptureSheet writes the durable retry copy before this function runs.
  }

  const message = savedToService
    ? kind === "note" && (!baseId || !filedToRequestedBase)
      ? filedToRequestedBase
        ? "Editable note preserved in Inbox. File it when its subject becomes clear."
        : "Editable note is safe in Inbox; its requested knowledge base was unavailable."
      : kind === "link"
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
