/**
 * Run `body` unless a run is still going; return the run in flight either way. The ref holds the
 * promise before the body can finish, and is cleared only if it still holds that promise, so a
 * body that completes without waiting does not leave a finished promise behind.
 */
export function runOnce(ref: { current: Promise<void> | null }, body: () => Promise<void>): Promise<void> {
  if (ref.current) return ref.current;
  const run = body();
  ref.current = run;
  // The caller gets the failure through `run`; this copy of it only clears the ref.
  void run.finally(() => {
    if (ref.current === run) ref.current = null;
  }).catch(() => undefined);
  return run;
}
