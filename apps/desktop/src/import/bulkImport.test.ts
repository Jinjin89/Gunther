import type { AssetCaptureResult } from "@gunther/contracts";
import { describe, expect, it, vi } from "vitest";
import { importable, importFiles, kindOf, titleOf } from "./bulkImport";

const file = (path: string, body = "x") => {
  const item = new File([body], path.split("/").pop()!);
  Object.defineProperty(item, "webkitRelativePath", { value: path });
  return item;
};
const result = (duplicate: boolean) => ({ importResult: { duplicate } }) as unknown as AssetCaptureResult;

describe("bulk import", () => {
  it("keeps readable files and leaves out hidden, empty and unknown ones", () => {
    const picked = importable([
      file("Papers/b.pdf"), file("Papers/a.pdf"), file("Papers/.DS_Store"), file("Papers/.git/x.md"),
      file("Papers/notes.md"), file("Papers/empty.pdf", ""), file("Papers/tool.exe"), file("Papers/fig.PNG"),
    ]);
    expect(picked.files.map((item) => item.name)).toEqual(["a.pdf", "b.pdf", "fig.PNG", "notes.md"]);
    expect(picked.skipped).toBe(4);
    expect(kindOf(file("x.pdf"))).toBe("paper");
    expect(kindOf(file("x.PNG"))).toBe("image");
    expect(kindOf(file("x.md"))).toBe("file");
    expect(titleOf(file("Wang et al. 2024.pdf"))).toBe("Wang et al. 2024");
  });

  it("uploads a few at a time and counts copies and failures", async () => {
    let running = 0;
    let peak = 0;
    const upload = vi.fn(async (item: File) => {
      running += 1; peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, 5));
      running -= 1;
      if (item.name === "bad.pdf") throw new Error("Storage is full");
      return result(item.name === "copy.pdf");
    });
    const seen: number[] = [];
    const files = ["a.pdf", "b.pdf", "copy.pdf", "bad.pdf", "c.pdf", "d.pdf"].map((name) => file(`Lit/${name}`));
    const done = await importFiles(files, { knowledgeBaseId: "lib", upload, concurrency: 2, onProgress: (progress) => seen.push(progress.done) });
    expect(peak).toBe(2);
    expect(done).toMatchObject({ total: 6, done: 6, added: 4, duplicates: 1, cancelled: false });
    expect(done.failed).toEqual([{ name: "Lit/bad.pdf", reason: "Storage is full" }]);
    expect(seen.at(-1)).toBe(6);
    expect(upload).toHaveBeenCalledWith(files[0], "a", "paper", "lib", "");
  });

  it("stops starting new uploads when cancelled", async () => {
    const controller = new AbortController();
    const upload = vi.fn(async () => { controller.abort(); return result(false); });
    const done = await importFiles([file("a.pdf"), file("b.pdf"), file("c.pdf")], { knowledgeBaseId: "lib", upload, concurrency: 1, signal: controller.signal, onProgress: () => undefined });
    expect(upload).toHaveBeenCalledOnce();
    expect(done).toMatchObject({ done: 1, cancelled: true });
  });
});
