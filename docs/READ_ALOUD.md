# Read aloud (text-to-speech)

Answers can be spoken: a **Listen** button under every answer (Ask page and Home), and an optional
**Read new answers automatically** switch (Settings → Read aloud) for a conversational feel.

## Pipeline

message text → **narrator** (`tts_narration.py`) → pieces ≤ the supplier's limit → supplier audio
(`tts_providers.py`) → one WAV kept in `SPEECH_DIR` (default `data/speech/`) and listed in `speech_clips`.

- **Long answers play as they are made.** The first reading is split into parts (a paragraph each, short
  ones joined; `split_for_listening`). `POST …/speech/begin` returns either the kept clip or a job;
  the app fetches `GET /speech/jobs/{id}/parts/{n}` in order, playing part 1 while part 2 is made.
  When the last part is done the parts are joined and kept as the clip, so later listens are one file.
  Jobs live in memory only (the last few).
- **The player.** Idle, an answer shows one *Listen* button. While reading it opens into a small player:
  pause/resume, progress (by part for long answers, time for a kept clip), *Record again*
  (`…/speech/begin?fresh=true`: drops this voice's kept clip and makes it anew), *What is spoken*
  (`/speech/clips/{id}/script`, or `/speech/jobs/{id}/script` while it is being made) and *Stop*.
- **Connected.** A sample that speaks is remembered in the settings file (`checks["tts:<provider>"]`) with
  a fingerprint of the address and key, so Settings shows *Connected* until either changes; a failed
  sample shows *Needs attention* with the supplier's message.
- **Cache key** = narration version + supplier + model + options (voice, language…) + message text.
  The second listen only plays the file. A new voice makes a new clip; *Delete* in Settings clears all.
- **Tables, pictures, code, formulas** are not read as written. A language model (Analysis, else Ask;
  a vision-capable one when the answer carries an embedded picture) describes each in the answer's
  language, and the description is spoken. No model, or it fails → an error with Retry, never a guess.
  A supplier with `reads_structure=True` skips this. Only `data:` pictures are shown to the model; the
  backend never fetches other addresses.
- Plain text is cleaned by rule: markdown marks, `[1]` citations and links' URLs are dropped.

## Settings: providers and jobs

Laid out like Transcription. **Providers** are connections you add (a supplier, its address, a key,
the models chosen from it); two of one kind are fine, e.g. Beijing and international Qwen accounts.
**Used for** lists the jobs; *Answers* (Listen and automatic reading) picks a model, or Off, and the
voice options of that model's supplier. Settings saved before this shape (one entry per supplier and
an `active` one) are converted when read (`tts_service.upgrade_config`).

## Adding a supplier

Add a `TtsProvider` to `PROVIDERS` in `tts_providers.py`: models, its own `options` (voice, tone…;
the Settings form draws them), `max_chars`, and an async `synthesize(config, text) -> wav bytes`.
No UI or API change is needed. Keys live in `service-settings.json` (0600) and are never sent back;
an unset Qwen key falls back to the key saved for Qwen models or transcription, and a new Qwen
provider starts in that key's region.

## API (desktop owner only)

`GET /settings/tts` · `POST|PUT|DELETE /settings/tts/providers[/{id}]` · `PUT /settings/tts/roles`
· `POST /settings/tts/sample` (a provider being edited, or the Answers job) · `DELETE /settings/tts/cache`
· `POST /sessions/{sid}/messages/{mid}/speech` → clip · `GET /speech/clips/{id}/audio|script`
