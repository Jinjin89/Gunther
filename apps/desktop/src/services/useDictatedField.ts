import { useEffect, useId, useRef } from "react";
import { useShortcut } from "../shortcuts/shortcuts";
import { useDictation } from "./useDictation";

/** The field ⌘⇧M speaks into: the one last focused, or else the last one on screen. */
let target: string | null = null;
const mounted: string[] = [];

const CJK = /^[　-鿿＀-￯]/;

/**
 * Dictation into a controlled text field. Finished phrases are appended to the
 * value; the ⌘⇧M shortcut toggles listening for whichever field is current.
 * Call `claim` from the field's focus handler so the shortcut follows the cursor.
 */
export function useDictatedField(value: string, onChange: (next: string) => void, options: { enabled?: boolean; onAppended?: (next: string) => void } = {}) {
  const id = useId();
  const latest = useRef(value);
  latest.current = value;
  const dictation = useDictation((text) => {
    const before = latest.current;
    const spacer = before && !/\s$/.test(before) && !CJK.test(text) ? " " : "";
    latest.current = `${before}${spacer}${text}`;
    onChange(latest.current);
    options.onAppended?.(latest.current);
  });
  const listening = dictation.state === "listening" || dictation.state === "starting";
  const enabled = options.enabled ?? true;

  useEffect(() => {
    if (!enabled) return undefined;
    mounted.push(id);
    target = id;
    return () => {
      mounted.splice(mounted.indexOf(id), 1);
      if (target === id) target = mounted[mounted.length - 1] ?? null;
    };
  }, [enabled, id]);

  const toggle = () => {
    if (listening) dictation.stop();
    else if (dictation.state === "idle") void dictation.start();
  };
  useShortcut("mod+shift+m", () => { if (target === id) toggle(); }, { enabled, allowInInputs: true });

  return { ...dictation, listening, dictating: dictation.state !== "idle", toggle, discard: dictation.cancel, claim: () => { target = id; } };
}
