import type { OutputVoices, VoicePair } from "@gunther/contracts";
import { useId } from "react";
import { finishLineEdit } from "./PreferenceFields";

const names = ["voice-a", "voice-b"] as const;

export function readVoices(data: FormData): VoicePair | undefined {
  const [a, b] = names.map((name) => String(data.get(name) ?? "").trim());
  if (a && b) return [a, b];
}

type VoiceFieldsProps = { options: OutputVoices; disabled?: boolean } & (
  | { initial?: VoicePair | null | undefined; value?: never; onChange?: never }
  | { value: VoicePair | null; onChange: (voices: VoicePair) => void; initial?: never }
);

export function VoiceFields({ options, value, onChange, initial, disabled }: VoiceFieldsProps) {
  const id = useId();
  const { choices, defaults, allowCustom } = options;
  if (!defaults && !allowCustom && !choices.length) return null;
  const available = (entry: string) => allowCustom || choices.some((choice) => choice.value === entry);
  const fallback: VoicePair = defaults ?? ["", ""];
  const pair = onChange ? value ?? fallback : fallback.map((entry, index) => {
    const saved = initial?.[index];
    return saved && available(saved) ? saved : entry;
  }) as VoicePair;
  return <>
    {names.map((name, index) => {
      const current = pair[index] ?? "";
      const control = onChange ? {
        value: current, onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
          const next: VoicePair = [...pair];
          next[index] = event.currentTarget.value;
          onChange(next);
        },
      } : { defaultValue: current };
      return <label key={name}>Speaker {index === 0 ? "A" : "B"} voice
        {allowCustom
          ? <input name={name} {...control} list={id} maxLength={80} required spellCheck={false} disabled={disabled}
            onKeyDown={onChange ? finishLineEdit : undefined} />
          : <select name={name} {...control} disabled={disabled}>
            {!current && <option value="">Choose a voice</option>}
            {current && !available(current) && <option value={current}>{current} (saved)</option>}
            {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>}
      </label>;
    })}
    {allowCustom && <datalist id={id}>
      {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
    </datalist>}
  </>;
}
