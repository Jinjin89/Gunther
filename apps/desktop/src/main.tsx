import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import CaptureWindowApp from "./capture/CaptureWindowApp";
import { ensureBackendReady } from "./api";
import "./styles.css";
// The design system loads last so its tokens and components win over older layers.
import "./design/tokens.css";
import "./design/primitives.css";
import "./design/shell.css";
import "./design/home.css";
import "./design/pages.css";
import "./design/prose.css";
import "./design/item.css";
import "./design/shortcuts.css";
import "./design/legacy.css";
// Capture's new surface loads after the legacy layer so its layout wins.
import "./design/capture.css";
import { applyTheme, readThemePreference, resolveTheme } from "./design/theme";
import { installExternalLinkHandler } from "./externalLinks";

document.documentElement.dataset.runtime = "__TAURI_INTERNALS__" in window ? "native" : "web";
if ("__TAURI_INTERNALS__" in window) installExternalLinkHandler();
// Apply the saved appearance before the first paint so dark mode never flashes light.
applyTheme(resolveTheme(readThemePreference()));

const root = createRoot(document.getElementById("root")!);
const surface = new URLSearchParams(window.location.search).get("surface");

void ensureBackendReady()
  .then(() => {
    root.render(
      <StrictMode>
        {surface === "capture" ? <CaptureWindowApp /> : <App />}
      </StrictMode>,
    );
  })
  .catch(() => {
    root.render(
      <main className="startup-error" role="alert">
        <h1>Gunther could not start its private knowledge service</h1>
        <p>
          Another app may be using the local service port. Gunther did not send your captures or
          knowledge to that process.
        </p>
        <button type="button" onClick={() => window.location.reload()}>Try again</button>
      </main>,
    );
  });
