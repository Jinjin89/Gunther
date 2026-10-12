# Live transcription measurements

Developer tools for `docs/LIVE_TRANSCRIPT_PLAN.md`. Nothing in the app uses them. They call Qwen with the key in the repo's `.env` (`QWEN_STT_API_KEY`, and `QWEN_STT_BASE_URL` if your address isn't `https://maas.qianwenaiapi.com`), and the terms step uses the `LLM_*` model there. Run from `apps/backend`.

1. **Make the test lecture** (once; spoken sentences are cached):

   ```
   .venv/bin/python -m evals.transcript.make_sample
   ```

   `script.toml` is a made-up 2-minute lecture: two speakers, Chinese with English terms, paragraphs separated by longer pauses. Qwen's text-to-speech (`qwen3-tts-flash`) speaks it, then a room is added: a fan, people talking far away, four knocks, and the student asking from the back. Output: `evals/results/transcript/sample/{clean,noisy}.wav` and `reference.json` (the text and each sentence's true time).

2. **Compare the ways of transcribing it**:

   ```
   .venv/bin/python -m evals.transcript.compare [--audio noisy|clean] [--only today,stream,paragraph,final]
   ```

   - `today`: 3.2 s pieces cut by the clock, each sent alone (what `realtime.py` does now);
   - `stream`: Layer 1, the streaming model at real-time pace (`--params '{"...": ...}'` adds streaming parameters);
   - `paragraph`: Layer 2, each paragraph heard again by the HTTP model with the previous one as context;
   - `final`: Layer 3, terms from the whole transcript plus the paragraphs before and after.

   The score is the error rate: edits ÷ reference tokens, where a token is one Chinese character or one Latin word, ignoring punctuation and case. `--reuse <results.json>` reuses an earlier stream run. Results: `evals/results/transcript/<time>-<audio>[-label]/` (`results.json`, `report.md` with every transcript).

3. **Probe the streaming model's behaviour** (connect time, context updates, silence timeout, faster than real time, semantic sentence ends):

   ```
   .venv/bin/python -m evals.transcript.probe_stream [--trial connect context silence heartbeat fast semantic]
   ```

Speech made by text-to-speech is cleaner than a real room, so the absolute numbers are optimistic; compare methods with each other, and try a real recording when you have one (put it at `sample/noisy.wav` with a matching `reference.json`).

4. **Check that a thinking pause doesn't split a sentence** (one sentence in two halves, 3 s and 10 s apart):

   ```
   .venv/bin/python -m evals.transcript.probe_pause
   ```

5. **Group sentences into paragraphs by content** (the `LLM_*` model, prompt §5.2), whole and live:

   ```
   .venv/bin/python -m evals.transcript.probe_paragraphs [--run <results.json>]
   ```
