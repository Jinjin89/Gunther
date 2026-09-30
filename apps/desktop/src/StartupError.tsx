import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { describeError, log, logsFolder, showLogsFolder } from "./log";

const native = "__TAURI_INTERNALS__" in window;

/** Shown when the knowledge service did not start: why, where the logs are, and a way to try again. */
export function StartupError({ reason }: { reason: string }) {
  const [logs, setLogs] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);

  useEffect(() => {
    void logsFolder().then(setLogs);
  }, []);

  const retry = async () => {
    setTrying(true);
    try {
      // The service may have stopped: start it again, then connect afresh.
      if (native) await invoke("restart_backend");
    } catch (error) {
      log.error("startup", `Starting the service again failed: ${describeError(error)}`);
    }
    window.location.reload();
  };

  return <main className="startup-error" role="alert">
    <h1>Gunther could not start its private knowledge service</h1>
    <p>{reason}</p>
    <p>Nothing was sent anywhere.{logs ? <> What happened is in the log files in <code>{logs}</code>.</> : null}</p>
    <div className="startup-error-actions">
      <button type="button" disabled={trying} onClick={() => void retry()}>{trying ? "Starting…" : "Try again"}</button>
      {native && <button type="button" className="is-quiet" onClick={() => void showLogsFolder().catch(() => undefined)}>Show log files</button>}
    </div>
  </main>;
}
