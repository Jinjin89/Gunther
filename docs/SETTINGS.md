# Settings

Use Settings to configure providers, app behavior and diagnostics. The installed desktop stores model and service settings in its private config directory. The backend also accepts `SERVICE_SETTINGS_FILE` and environment defaults. Saved values take precedence. A successful save writes the file before applying changes to the running service.

## Models and services

Add a provider with its address, key and model IDs. Fetch models where the provider supports it, or enter an ID directly. Choose the model's vision and thinking capabilities and test the connection. Provider presets and defaults live in the registry. [Model registry](../apps/backend/gunther/model_registry.py)

| Job | Use |
| --- | --- |
| Analysis | Capture analysis, structure review, summaries and topic overviews |
| Ask | Answers and research turns |
| Outputs | Studio builds and work on saved reports and decks |
| Photos | Image-aware capture summaries |

Ask shows a missing model or key when it has no usable choice. Outputs resolves its own model and fallback. Ask conversations can switch model and effort. Gunther records the model for each answer and replays reasoning only where the chosen model's protocol needs it. The common effort scale runs from Off through Max. The gateway adapts it to the provider and reports the effort used. [Model gateway](../apps/backend/gunther/llm.py) · [Effort profiles](../apps/backend/gunther/model_profiles.py)

Services configures Tavily web search and capture summaries, including whether summaries may use photos. Test connection checks the current form values. Provider and service keys stay in the private settings file. The UI sees masked state. Paired phones can list allowed models and use their roles. Provider edits and diagnostics require desktop ownership.

## Transcription

Add a SenseVoice, Qwen, OpenAI or compatible transcription provider. Choose a model and language for Recording and Dictation independently. SenseVoice uses a configured local service. Qwen can use a live WebSocket task or the short-audio request supported by its selected provider. Compatible HTTP providers receive audio pieces. Desktop live dictation requests streaming for its own session. [Transcription registry](../apps/backend/gunther/speech_registry.py) · [Live transcript](CAPTURE.md#live-transcript-v2)

## Read aloud

Use Listen below an Ask answer or turn on automatic reading for new answers. Configure the Answers speech job, provider, model and voice. The narrator prepares a spoken script from the answer. Playback starts from the first available speech part, and the app can show the script and reuse a completed clip. Missing configuration or a synthesis failure appears in the player. Settings offers voice samples and a cache clear control. A replaced or failed sample releases its audio URL, and leaving the sample view releases the remaining URL. [Speech routes](../apps/backend/gunther/tts_api.py)

## Usage

Usage shows requests and reported input, cached, output and reasoning tokens by local day, provider/model and job. Pick Today, 7 days, 30 days or All, inspect recent requests or clear the history. The service records requests through the shared model gateway. Providers that omit token counts leave those fields unavailable. Studio shares a stable material prefix, puts the task after it and warms the prefix before running parallel requests. [Usage API](../apps/backend/gunther/usage_api.py) · [Studio requests](../apps/backend/gunther/studio/requests.py)

## Developer

Turn on Keep how answers are made to keep new Ask traces. Select an answer and open Trace to see steps and provider calls, including prompts, replies, timing and available usage. The service keeps the 200 most recent traces. Trace copy produces JSON, which can include private source text.

Logs shows the recent backend log lines and can reveal the log folder. Background jobs shows status, attempts and errors for queued work. These views are desktop owner tools. [Developer API](../apps/backend/gunther/developer_api.py) · [Log files](OPERATIONS.md#data-and-logs)
