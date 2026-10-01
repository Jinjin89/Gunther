# Models and service settings

Everything Gunther connects to is set up in the app, in **Settings**, rather
than in environment variables:

- **Models**: the language models, from as many providers as you like, and
  which one does each job.
- **Transcription**: the speech-to-text models, from as many providers as you
  like, and which one does recording and which does Ask dictation.
- **Services**: web search and summaries.

## Transcription

Set up like Models: **providers** first, then **jobs** that pick a model.

**Providers** are SenseVoice (private, on this computer or your network), Qwen
(Qwen3-ASR on Alibaba Cloud), OpenAI, or any server with an OpenAI-style
`/audio/transcriptions` (Groq, a Whisper you host). Each has an address, a key
(SenseVoice needs none) and its models; **Fetch models** lists what it offers.
Fetch models returns exactly what the supplier's own list names (`GET <address>/models`): Gunther
keeps no built-in model lists and adds or hides nothing. If the supplier returns no list, it says
so and the model's id is typed. Qwen's address is the Qwen AI platform's
`https://maas.qianwenaiapi.com/compatible-mode/v1`; only Qwen3-ASR-Flash answers on its
`chat/completions`, which Gunther uses, so `-filetrans` and real-time recognisers may be listed
but need other addresses.

**Jobs** pick one model each and a language. Recording writes the words in short segments as you
speak; Ask dictation sends the whole take when you stop (cut only past about two and a half
minutes). There is no Live switch. Audio is always saved on this computer first.

Until providers are saved, they are made from the older `STT_*`, `SENSEVOICE_*`
and `QWEN_STT_*` settings, and both jobs use the one they chose. Code:
`speech_registry.py` (providers, jobs, choosing the model), `speech_api.py`
(`/settings/speech…`, desktop owner only) and `realtime.py` (the live socket,
`/recordings/live?purpose=dictation`).

## Models

**Providers** are connections: DeepSeek, Kimi, GLM, Qwen, OpenAI, or a
self-hosted server (vLLM, Ollama, LM Studio, anything OpenAI-compatible). Each
has a name, a base URL, a key (optional for self-hosted), and the models chosen
from it. **Fetch models** lists what the provider offers; a model can also be
typed by id. Each model says whether it **sees images** and how its thinking is
set. Both are known for DeepSeek, Kimi, GLM, Qwen and OpenAI models, and can be
set by hand for any other.

**Jobs** say which model does what, at what thinking effort:

| Job | Does | Default |
| --- | --- | --- |
| Analysis | Reads captures; writes summaries, paper notes, topic overviews | DeepSeek `deepseek-flash`, Low |
| Ask | Answers questions (each conversation can switch) | DeepSeek `deepseek-flash`, High |
| Photos | Looks at photos you capture | the first model that sees images, Low |

`deepseek-flash` (DeepSeek V4.1 Flash) sees images and thinks, so one DeepSeek
key covers all three jobs.

### One thinking-effort scale

Everywhere in the app, effort is **Off · Low · Medium · High · Max**. Each model
turns that into its own setting (`apps/backend/gunther/model_profiles.py`):

| Model family | Off | Low | Medium | High | Max |
| --- | --- | --- | --- | --- | --- |
| DeepSeek (flash, v4-pro) | thinking disabled | low | → high | high | max |
| Kimi K3, GLM-5 (always think) | → low | low | → high | high | max |
| Kimi K2.6, GLM-4.x (on/off) | off | on | on | on | on |
| Qwen (token budget) | off | 2k | 8k | 16k | 32k |
| OpenAI GPT-5, most self-hosted | → low | low | medium | high | → high |
| Kimi K2.7 Code, reasoners | no setting | | | | |

When a model lacks the level asked for, the nearest one is used (the stronger
of two), asking for thinking never turns it off, and the answer says what was
used ("Flash has no Medium setting; used High"). A server that refuses the
setting is asked again without it, and the answer admits that too.

### Switching model mid-conversation

Ask's composer has a model menu and an effort menu, in every conversation and on
Home. A conversation continues with the model of its last answer; a new one
starts from this device's last choice, else the Ask job.

Switching is safe at any turn because Gunther keeps the conversation itself, in
a neutral form, and rebuilds each request for the model answering now:

- Each answer stores the text, its citations, the model and effort, and the
  model's reasoning, kept apart.
- A model's reasoning goes back only to that same model, and only if it needs
  it (Kimi K2.7 Code does; DeepSeek ignores it without tools).
- A model that cannot see images is not sent them; their recognised text stands
  in, and the answer says so.
- Every answer keeps the label of the model that wrote it.

A model that fails (bad key, out of credit, timeout) is named with the reason,
and the quotes stand in for its answer.

### How it is built

- `llm.py`: one gateway over **Chat Completions** for every provider (streamed,
  reasoning kept apart, JSON checked against a schema with one retry, errors in
  plain words). Every feature asks through it.
- `model_profiles.py`: dialects (how a model spells thinking) and the built-in
  table of which models speak which, see images, or need their reasoning back.
- `model_registry.py`: providers, presets, jobs; building the gateway.
- `models_api.py`: `/settings/models`, `/settings/providers…`,
  `/settings/model-roles` (desktop owner only) and `/models` (the model menu;
  any signed-in device, no keys or addresses).

Until providers are saved in the app, one is made from `LLM_*` (or the older
`DEEPSEEK_*`) settings, so an existing `.env` keeps working. A language model
saved by the first version of this page carries over the same way.

## Services

| Service | What it does | Settings |
| --- | --- | --- |
| Web search | Lets Ask look things up online, by Tavily | Tavily API key, depth, pages per search |
| Summaries | A summary of every capture | On/off, show photos to the model |

Each service and provider shows one of four states: **Set up** (**Connected**
once a connection test of those values passed), **Not set up**, **Needs
attention** (a test failed, or the choice cannot work), or **Off**. **Test
connection** tries the values on screen before they are saved.

`apps/backend/gunther/service_settings.py` declares every service: its fields,
how to tell its state, and how to test it. The Settings page draws itself from
these declarations, so adding a service is one new entry in `SERVICES`.

## Storage and security

- Everything saved in the app is written to `service-settings.json`: the desktop
  app's private data folder, or `data/` in a checkout (`SERVICE_SETTINGS_FILE`
  overrides). It wins over `.env` and the environment; a value set only in
  `.env` is marked “from .env”.
- Keys live in that file, readable only by the user (mode 600), like other
  desktop apps keep theirs; Gunther deliberately does not use the system
  keychain.
- Saving applies at once, without a restart, in the desktop app and the phone
  gateway alike.
- Keys never go back to the app: it sees whether one is set and its last four
  characters. A paired phone can list the models to pick one in Ask, but cannot
  see or change any setting.
