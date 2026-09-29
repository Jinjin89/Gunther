import type { AssetCaptureResult, SourceKind } from "@gunther/contracts";

/** Files Gunther can read text from, or at least keep as an original with a preview. */
const IMPORTABLE = /\.(pdf|docx|epub|md|markdown|txt|html?|csv|tsv|json|xml|png|jpe?g|gif|webp|tiff?|bmp)$/i;
const IMAGE = /\.(png|jpe?g|gif|webp|tiff?|bmp)$/i;

/** A file's place in the chosen folder, when the browser gives one. */
const pathOf = (file: File) => (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;

export interface ImportSelection {
  files: File[];
  /** Hidden, empty or unreadable kinds of file that were left out. */
  skipped: number;
}

/** The files worth importing from a picked folder, in path order. */
export function importable(files: Iterable<File>): ImportSelection {
  const chosen: File[] = [];
  let skipped = 0;
  for (const file of files) {
    const hidden = pathOf(file).split("/").some((part) => part.startsWith("."));
    if (hidden || file.size === 0 || !IMPORTABLE.test(file.name)) skipped += 1;
    else chosen.push(file);
  }
  chosen.sort((a, b) => pathOf(a).localeCompare(pathOf(b)));
  return { files: chosen, skipped };
}

export function kindOf(file: File): SourceKind {
  if (IMAGE.test(file.name)) return "image";
  return /\.pdf$/i.test(file.name) ? "paper" : "file";
}

/** The name a source starts with; Gunther retitles a paper from its own first page. */
export const titleOf = (file: File) => file.name.replace(/\.[^.]+$/, "").trim().slice(0, 160) || file.name.slice(0, 160);

export interface ImportProgress {
  total: number;
  done: number;
  added: number;
  /** Already in Gunther: the same file was captured before. */
  duplicates: number;
  failed: { name: string; reason: string }[];
  cancelled: boolean;
}

export type Upload = (file: File, title: string, kind: SourceKind, knowledgeBaseId: string | null, notes: string) => Promise<AssetCaptureResult>;

/**
 * Upload files a few at a time. Each is saved as it arrives; reading and
 * indexing happen later in the background, so a thousand papers import in
 * minutes and are searchable as the worker gets to them.
 */
export async function importFiles(files: File[], options: {
  knowledgeBaseId: string;
  upload: Upload;
  onProgress: (progress: ImportProgress) => void;
  signal?: AbortSignal;
  concurrency?: number;
}): Promise<ImportProgress> {
  const progress: ImportProgress = { total: files.length, done: 0, added: 0, duplicates: 0, failed: [], cancelled: false };
  let next = 0;
  const worker = async () => {
    while (next < files.length) {
      if (options.signal?.aborted) { progress.cancelled = true; return; }
      const file = files[next++]!;
      try {
        const result = await options.upload(file, titleOf(file), kindOf(file), options.knowledgeBaseId, "");
        if (result.importResult.duplicate) progress.duplicates += 1;
        else progress.added += 1;
      } catch (reason) {
        progress.failed.push({ name: pathOf(file), reason: reason instanceof Error ? reason.message : "Upload failed" });
      }
      progress.done += 1;
      options.onProgress({ ...progress, failed: [...progress.failed] });
    }
  };
  await Promise.all(Array.from({ length: Math.min(options.concurrency ?? 3, files.length) }, worker));
  if (options.signal?.aborted) progress.cancelled = true;
  return progress;
}
