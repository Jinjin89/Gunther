# Read aloud (text-to-speech)

Answers can be spoken: a **Listen** button under every answer (Ask page and Home), and an optional
**Read new answers automatically** switch (Settings → Read aloud) for a conversational feel.

## Pipeline

message text → **narrator** (`tts_narration.py`) → pieces ≤ the supplier's limit → supplier audio
(`tts_providers.py`) → one WAV kept in `SPEECH_DIR` (default `data/speech/`) and listed in `speech_clips`.

- **Cache key** = narration version + supplier + model + options (voice, language…) + message text.
  The second listen only plays the file. A new voice makes a new clip; *Delete* in Settings clears all.
- **Tables, pictures, code, formulas** are not read as written. A language model (Analysis, else Ask;
  a vision-capable one when the answer carries an embedded picture) describes each in the answer's
  language, and the description is spoken. No model, or it fails → an error with Retry, never a guess.
  A supplier with `reads_structure=True` skips this. Only `data:` pictures are shown to the model; the
  backend never fetches other addresses.
- Plain text is cleaned by rule: markdown marks, `[1]` citations and links' URLs are dropped.

## Adding a supplier

Add a `TtsProvider` to `PROVIDERS` in `tts_providers.py`: models, its own `options` (voice, tone…;
the Settings form draws them), `max_chars`, and an async `synthesize(config, text) -> wav bytes`.
No UI or API change is needed. Keys live in `service-settings.json` (0600) and are never sent back;
an unset Qwen key falls back to the key saved for Qwen models or transcription.

## API (desktop owner only)

`GET/PUT /settings/tts` · `POST /settings/tts/sample` (hear unsaved settings) · `DELETE /settings/tts/cache`
· `POST /sessions/{sid}/messages/{mid}/speech` → clip · `GET /speech/clips/{id}/audio|script`
