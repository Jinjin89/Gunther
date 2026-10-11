import { invoke } from "@tauri-apps/api/core";
import { log } from "./log";

const native = () => "__TAURI_INTERNALS__" in window;
let pending: Promise<void> = Promise.resolve();
const failedWrites = new Map<string, string | null>();

function persist(key: string, value: string | null) {
  if (!native() || !key.startsWith("gunther:")) return;
  pending = pending.then(async () => {
    try {
      await invoke("desktop_storage_set", { key, value });
      failedWrites.delete(key);
    } catch (error) {
      failedWrites.set(key, value);
      log.error("storage", "A preference or pending capture could not be saved to disk.");
    }
  });
}

/** Browser storage is a cache. Native files survive replacing or reinstalling the app. */
export const persistentStorage: Storage = {
  get length() { return window.localStorage.length; },
  key: (index) => window.localStorage.key(index),
  getItem: (key) => window.localStorage.getItem(key),
  setItem(key, value) { window.localStorage.setItem(key, value); persist(key, value); },
  removeItem(key) { window.localStorage.removeItem(key); persist(key, null); },
  clear() {
    const keys = Object.keys(window.localStorage);
    window.localStorage.clear();
    for (const key of keys) persist(key, null);
  },
};

export async function flushPersistentStorage(): Promise<void> {
  await pending;
  for (const [key, value] of failedWrites) {
    await invoke("desktop_storage_set", { key, value });
    failedWrites.delete(key);
  }
}

export async function initializePersistentStorage(): Promise<void> {
  if (!native()) return;
  const legacy = Object.fromEntries(Object.keys(window.localStorage)
    .filter((key) => key.startsWith("gunther:"))
    .map((key) => [key, window.localStorage.getItem(key)!]));
  const saved = await invoke<Record<string, string>>("desktop_storage_load", { legacy });
  for (const key of Object.keys(legacy)) window.localStorage.removeItem(key);
  for (const [key, value] of Object.entries(saved)) window.localStorage.setItem(key, value);
}
