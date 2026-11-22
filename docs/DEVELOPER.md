# Developer guide

## Setup

From a fresh checkout, install Node.js 22.12+, Python 3.12+, uv and Rust. Tauri also needs the platform development tools. The Linux release job lists its system packages. For mobile, use Flutter with the Android SDK, or full Xcode and the iOS SDK. The CI configuration pins the Flutter version. [Desktop release workflow](../.github/workflows/release.yml) · [Quality workflow](../.github/workflows/quality.yml)

```sh
cp .env.example .env
npm ci
uv sync --project apps/backend
npm run models:fetch
npm run dev
```

`npm run dev` starts the development API on port 8787 and the Tauri window. Use `npm run dev:backend` and `npm run dev:desktop:web` in separate terminals for browser work. Vite proxies the API during web development. Configure model keys in a private `.env` or in [Settings](SETTINGS.md). The local embeddings and pause detector are optional for work on lexical retrieval and fixed-duration speech segments.

For a phone build, run `npm run mobile:get`, then `cd apps/mobile && flutter run`. The emulator defaults are `10.0.2.2:8787` on Android and `127.0.0.1:8787` on iOS. Physical devices use pairing. [Mobile app](../apps/mobile/README.md) · [Capture](CAPTURE.md#mobile-capture)

## Checks

| What | Command from the repository root |
| --- | --- |
| Backend lint and desktop types | `npm run lint` |
| Backend tests | `npm run test:backend` |
| Desktop tests | `npm run test:desktop` |
| Rust compile and tests | `npm run check:desktop` and `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml` |
| Flutter analysis and tests | `npm run mobile:analyze` and `npm run mobile:test` |
| Desktop production bundle | `npm run build:desktop:web` |
| Browser journey | `npm run smoke:desktop-ui` |
| Combined desktop and backend gate | `npm run check` |
| Build and parser script tests | `node --test scripts/build-step.test.mjs scripts/parser-inputs.test.mjs` |

The browser journey runs against temporary data with a fixture model provider and real retrieval and saves. Install Chrome or Chromium, or set `CHROME_BIN` to its executable. Build the sidecar resource first when the platform's Tauri check needs it, using `npm run prepare:sidecar -w @gunther/desktop`.

## Fixtures and evals

Startup opens the configured workspace. To seed a disposable development database explicitly, run:

```sh
PYTHONPATH=apps/backend uv run --project apps/backend python scripts/seed_demo.py \
  /tmp/gunther-demo.sqlite scripts/fixtures/demo.json
```

Point `DATABASE_URL` at that database when using the fixture. The evals keep their own materials and write results under `apps/backend/evals/results/`. Runs that call models or fetch public pages use the configured services.

| Eval | Guide and entry point from `apps/backend` |
| --- | --- |
| Ask | [Ask exam](../apps/backend/evals/README.md), `.venv/bin/python -m evals.ask.run --label dev` |
| Artifacts turns | [Artifacts exam](../apps/backend/evals/artifacts/README.md), `.venv/bin/python -m evals.artifacts.run --path baseline` |
| Build quality | [Quality eval](../apps/backend/evals/quality/README.md), `.venv/bin/python -m evals.quality.run --sets velmora --label dev` |
| Slide rendering and review | [Slide evals](../apps/backend/evals/slides/README.md) |
| Speech and paragraph grouping | [Transcript evals](../apps/backend/evals/transcript/README.md) |

## Packaging

- Run `npm run build:desktop` on the target platform. It builds the desktop bundle, Rust shell and PyInstaller Helper. Use `npm run build:desktop:web` when you need only the web assets.
- `node scripts/parser-inputs.mjs` produces the parser input key from the shared manifest. To build the offline runtime, run `uv run --frozen --project tools/document-parser scripts/build-document-parser.py`. Packaging and preflight check the bundle manifest and parser lock hash. [Parser input manifest](../scripts/parser-inputs.mjs) · [Parser builder](../scripts/build-document-parser.py)
- To check Studio in a packaged Helper, supply the deck explicitly. `gunther-backend --studio-smoke scripts/fixtures/studio-package.md`. This imports Studio and checks the fixture without an active workspace or model.
- To verify release output, follow [Operations](OPERATIONS.md#release). For a mobile developer APK, run `flutter build apk --debug` from `apps/mobile`.

## Repository layout

| Path | Contents |
| --- | --- |
| [apps/backend](../apps/backend/) | Python API, domain modules, tests and evals |
| [apps/desktop](../apps/desktop/) | React UI, render and export code, Tauri shell in `src-tauri/` |
| [apps/mobile](../apps/mobile/) | Flutter app, Android and iOS share ingress |
| [packages/contracts](../packages/contracts/) | API types used by the desktop |
| [scripts](../scripts/) | Build, fixture, smoke, backup and restore commands |

See [Architecture](ARCHITECTURE.md) for job owners and [Settings → Developer](SETTINGS.md#developer) for the diagnostics in the app.
