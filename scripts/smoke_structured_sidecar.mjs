/** Verify the packaged helper against disposable data; never opens the user's app. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { access, mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const root = path.resolve(import.meta.dirname, "..");
const executable = process.argv[2] ?? path.join(root,
  "apps/desktop/src-tauri/target/release/bundle/macos/Gunther.app/Contents/Helpers/GuntherBackend.app/Contents/MacOS/GuntherBackend");
await access(executable);
const probe = net.createServer();
await new Promise((resolve, reject) => { probe.once("error", reject); probe.listen(0, "127.0.0.1", resolve); });
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
// macOS /var and /tmp are aliases. Use a physical path, as the helper correctly
// rejects managed storage beneath symlink components.
const data = await mkdtemp(path.join(await realpath(os.tmpdir()), "gunther-structured-smoke-"));
const nonce = randomBytes(32).toString("hex");
const tokenFile = path.join(data, "backend-ready", `backend-auth-token.${nonce}`);
const environment = { ...process.env, DEEPSEEK_API_KEY: "", LLM_API_KEY: "", STT_PROVIDER: "auto", STT_BASE_URL: "", OCR_PROVIDER: "disabled" };
for (const name of ["EMBEDDING_MODEL_PATH", "DOCLING_PYTHON", "DOCLING_ARTIFACTS_PATH"]) delete environment[name];
const helper = spawn(executable, ["--data-dir", data, "--port", String(port), "--launch-nonce", nonce, "--disable-mobile-gateway"], { env: environment, stdio: "ignore" });
let startupError;
helper.once("error", (error) => { startupError = error; });
try {
  let token;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (startupError) throw startupError;
    if (helper.exitCode !== null) throw new Error(`Helper exited with ${helper.exitCode}`);
    const ready = await readFile(tokenFile, "utf8").catch(() => null);
    if (ready) {
      const [publishedNonce, secret] = ready.trim().split("\n");
      assert.ok(publishedNonce === nonce && /^[A-Za-z0-9_-]{43}$/.test(secret), "Invalid readiness token format");
      token = secret;
      break;
    }
    await delay(100);
  }
  assert.ok(token, "Helper did not publish its readiness token");
  const origin = `http://127.0.0.1:${port}/api`;
  const request = async (route, options = {}) => {
    const response = await fetch(origin + route, {
      ...options, headers: { "X-Gunther-Token": token, "Content-Type": "application/json", ...options.headers },
      signal: AbortSignal.timeout(10_000),
    });
    assert.ok(response.ok, `${route}: HTTP ${response.status}`);
    return response.json();
  };
  const post = (route, value) => request(route, { method: "POST", body: JSON.stringify(value) });
  assert.equal((await fetch(origin + "/retrieval/status")).status, 401);
  const library = await post("/knowledge-bases", { title: "Packaged smoke", question: "What supports this claim?", description: "Disposable verification data" });
  const captured = await request(`/captures/assets?title=Methods&fileName=methods.md&kind=file&knowledgeBaseId=${library.id}&deferProcessing=true`, {
    method: "POST", headers: { "Content-Type": "text/markdown" },
    body: "# Methods\nGenome quality requires careful controls.\n猫咪需要均衡饮食。",
  });
  const source = captured.importResult.source;
  let structure;
  for (let attempt = 0; attempt < 100; attempt++) {
    structure = await request(`/sources/${source.id}/structure`);
    if (structure.processing.state === "ready") break;
    assert.notEqual(structure.processing.state, "failed");
    await delay(100);
  }
  assert.equal(structure.processing.state, "ready");
  const block = structure.blocks.find((item) => item.content.startsWith("Genome quality"));
  assert.ok(block?.parentId, "Document hierarchy was not retained");
  const topic = await post(`/knowledge-bases/${library.id}/topics`, { title: "Quality" });
  await post(`/knowledge-bases/${library.id}/topics/${topic.id}/evidence`, { blockId: block.id });
  const session = await post(`/knowledge-bases/${library.id}/sessions`, { focusChapterId: topic.id });
  const answer = await post(`/sessions/${session.id}/messages`, { content: "Genome quality controls" });
  assert.deepEqual(answer.assistantMessage.citations.map((citation) => citation.blockId), [block.id]);
  const citation = await request(`/sources/${source.id}/structure?revision_id=${structure.revisionId}&block_id=${block.id}`);
  assert.equal(citation.blocks[0].content, block.content);
  // The bundled model and sqlite-vec: semantic search is on without setup, and a
  // Chinese question reaches the English passage no keyword shares.
  let retrieval;
  for (let attempt = 0; attempt < 300; attempt++) {
    retrieval = await request("/retrieval/status");
    if (retrieval.embeddingJobs === 0 && retrieval.embeddedBlocks === retrieval.passages) break;
    await delay(100);
  }
  assert.equal(retrieval.semanticConfigured, true, retrieval.semanticOffReason ?? "semantic search is off");
  assert.ok(retrieval.passages > 0 && retrieval.embeddedBlocks === retrieval.passages, "Passages were not embedded");
  const open = await post(`/knowledge-bases/${library.id}/sessions`, {});
  const semantic = await post(`/sessions/${open.id}/messages`, { content: "如何保证基因组的质量？" });
  assert.equal(semantic.assistantMessage.citations[0]?.blockId, block.id, "Semantic search missed the passage");
  console.log("Packaged helper passed: authentication, durable upload, worker, document tree, topics, scoped FTS, pinned citations and bundled semantic search.");
} finally {
  helper.kill("SIGINT");
  for (let attempt = 0; attempt < 50 && helper.exitCode === null && helper.signalCode === null; attempt++) await delay(100);
  if (!startupError && helper.exitCode === null && helper.signalCode === null) {
    helper.kill("SIGKILL");
    await new Promise((resolve) => helper.once("exit", resolve));
  }
  await rm(data, { recursive: true, force: true });
}
