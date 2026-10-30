import { useEffect, useId, useRef } from "react";
import { useShortcut } from "../shortcuts/shortcuts";
import { useDictation } from "./useDictation";
import { DictationInsertion } from "./dictationText";

/** The field ⌘⇧M speaks into: the one last focused, or else the last one on screen. */
let target: string | null = null;
const mounted: string[] = [];

/**
 * Dictation into a controlled text field. ASR revisions replace their own region;
 * the ⌘⇧M shortcut toggles listening for whichever field is current.
 * Call `claim` from the field's focus handler so the shortcut follows the cursor.
 */
export function useDictatedField(value: string, onChange: (next: string) => void, options: { enabled?: boolean; onAppended?: (next: string) => void } = {}) {
  const id = useId();
  const latest = useRef(value);
  latest.current = value;
  const insertion = useRef(new DictationInsertion());
  const dictation = useDictation((_text, pieces) => {
    latest.current = insertion.current.update(latest.current, pieces);
    onChange(latest.current);
    options.onAppended?.(latest.current);
  }, { onStart: () => insertion.current.begin(latest.current) });
  const listening = dictation.state === "listening" || dictation.state === "starting";
  const enabled = options.enabled ?? true;

  useEffect(() => { if (!enabled) dictation.cancel(); }, [enabled, dictation.cancel]);

  useEffect(() => {
    if (!enabled) return undefined;
    mounted.push(id);
    target = id;
    return () => {
      mounted.splice(mounted.indexOf(id), 1);
      if (target === id) target = mounted[mounted.length - 1] ?? null;
    };
  }, [enabled, id]);

  const toggle = dictation.toggle;
  useShortcut("mod+shift+m", () => { if (target === id) toggle(); }, { enabled, allowInInputs: true });

  return { ...dictation, listening, dictating: dictation.state !== "idle", toggle, discard: dictation.cancel, claim: () => { target = id; } };
}
