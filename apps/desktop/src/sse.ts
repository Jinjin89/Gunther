/** Server-sent events, read from a fetch response body. */
export interface ServerEvent {
  event: string;
  data: string;
}

/** Yields each complete event as it arrives; a partial one waits for the rest. */
export async function* readServerEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ServerEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let endsWithCR = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      const chunk = decoder.decode(value, { stream: !done });
      if (chunk) {
        // A CR ends its line immediately. The next chunk may start with its paired LF.
        const text = endsWithCR && chunk.startsWith("\n") ? chunk.slice(1) : chunk;
        endsWithCR = chunk.endsWith("\r");
        buffer += text.replace(/\r\n?/g, "\n");
      }
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (data.length) yield { event, data: data.join("\n") };
        end = buffer.indexOf("\n\n");
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

export class RunStreamError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "RunStreamError";
  }
}

export interface RunTerminal {
  stopped: () => Error;
  failed: string;
  closed: string;
  error?: (message: string, status: number | undefined, payload: unknown) => Error;
}

/** Decode one run until its result, keeping each channel's progress and failure types. */
export async function readRunEvents<Result, Progress>(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: Progress) => void,
  terminal: RunTerminal,
): Promise<Result> {
  for await (const message of readServerEvents(body)) {
    const data: unknown = JSON.parse(message.data);
    if (message.event === "done") return data as Result;
    if (message.event === "stopped") throw terminal.stopped();
    if (message.event === "error") {
      const payload = data as { detail?: unknown; status?: unknown } | null;
      const detail = typeof payload?.detail === "string" ? payload.detail : terminal.failed;
      const status = typeof payload?.status === "number" ? payload.status : undefined;
      throw terminal.error?.(detail, status, data) ?? new RunStreamError(detail, status);
    }
    onEvent(data as Progress);
  }
  throw new Error(terminal.closed);
}
