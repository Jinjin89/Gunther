import type { OutputVoices, VoicePair } from "@gunther/contracts";
import { useId } from "react";

const names = ["voice-a", "voice-b"] as const;

export function readVoices(data: FormData): VoicePair | undefined {
  const [a, b] = names.map((name) => String(data.get(name) ?? "").trim());
  if (a && b) return [a, b];
}

export function VoiceFields({ options, initial }: { options: OutputVoices; initial?: VoicePair | null | undefined }) {
  const id = useId();
  const { choices, defaults, allowCustom } = options;
  if (!defaults) return null;
  return <>
    {names.map((name, index) => {
      const saved = initial?.[index];
      const value = saved && (allowCustom || choices.some((choice) => choice.value === saved)) ? saved : defaults[index];
      return <label key={name}>Speaker {index === 0 ? "A" : "B"} voice
        {allowCustom
          ? <input name={name} defaultValue={value} list={id} maxLength={80} required spellCheck={false} />
          : <select name={name} defaultValue={value}>
            {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>}
      </label>;
    })}
    {allowCustom && <datalist id={id}>
      {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
    </datalist>}
  </>;
}
