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
import "./design/trash.css";
import "./design/legacy.css";
import "./design/services.css";
import "./design/models.css";
import "./design/agent.css";
import "./design/typography.css";
// Capture's new surface loads after the legacy layer so its layout wins.
import "./design/capture.css";
import { applyTheme, readThemePreference, resolveTheme } from "./design/theme";
import { applyTypography } from "./design/typography";
import { installExternalLinkHandler } from "./externalLinks";
import { StartupError } from "./StartupError";
import { describeError, installLogForwarding, log } from "./log";

document.documentElement.dataset.runtime = "__TAURI_INTERNALS__" in window ? "native" : "web";
installLogForwarding();
if ("__TAURI_INTERNALS__" in window) installExternalLinkHandler();
// Apply the saved appearance before the first paint so dark mode never flashes light.
applyTheme(resolveTheme(readThemePreference()));
applyTypography();

const root = createRoot(document.getElementById("root")!);
const surface = new URLSearchParams(window.location.search).get("surface");
const opened = performance.now();
log.info("startup", `${surface ?? "main"} window opened`);

void ensureBackendReady()
  .then(() => {
    log.info("startup", `${surface ?? "main"} window connected after ${Math.round(performance.now() - opened)} ms`);
    root.render(
      <StrictMode>
        {surface === "capture" ? <CaptureWindowApp /> : <App />}
      </StrictMode>,
    );
  })
  .catch((reason: unknown) => {
    log.error("startup", `${surface ?? "main"} window could not connect: ${describeError(reason)}`);
    root.render(<StartupError reason={reason instanceof Error ? reason.message : String(reason)} />);
  });
