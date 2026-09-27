import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import CaptureWindowApp from "./capture/CaptureWindowApp";
import { ensureBackendReady } from "./api";
import "./styles.css";

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
