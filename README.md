# Gunther

Gunther is a local knowledge workspace. Capture notes, web pages, documents, photos and recordings. File them in libraries, search their readings, ask questions with citations, and turn selected material into reports or slide decks. The Flutter app captures to the same workspace through a paired connection.

## Run from source

Install Node.js 22.12 or later, Python 3.12 or later, uv, Rust, and the Tauri system dependencies for your platform. From a fresh checkout:

```sh
cp .env.example .env
npm ci
uv sync --project apps/backend
npm run dev
```

The development API listens on `127.0.0.1:8787`. Run `npm run models:fetch` to install the local search and speech pause-detection models. Set up optional model and web providers in Settings. See the [developer guide](docs/DEVELOPER.md) for browser-only development, Flutter and packaging.

## Guides

| Topic | Guide |
| --- | --- |
| Find, file and read sources | [Library](docs/LIBRARY.md) |
| Save material and record audio | [Capture](docs/CAPTURE.md) |
| Ask with evidence | [Ask](docs/ASK.md) |
| Make, revise and check reports and decks | [Outputs](docs/OUTPUTS.md) |
| Configure providers and inspect usage | [Settings](docs/SETTINGS.md) |
| Locate modules and understand data flow | [Architecture](docs/ARCHITECTURE.md) |
| Set up, test and build | [Developer](docs/DEVELOPER.md) |
| Use the desktop visual and interaction rules | [Design system](docs/DESIGN_SYSTEM.md) |
| Read the deck and report grammar | [Studio format](docs/STUDIO_FORMAT.md) |
| Diagnose, back up, restore and release | [Operations](docs/OPERATIONS.md) |
| Agreed future work | [Facts and Steps](docs/FACTS_AND_STEPS_PLAN.md), [Next projects](docs/NEXT.md) |
