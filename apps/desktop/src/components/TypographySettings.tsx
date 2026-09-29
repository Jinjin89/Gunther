import { useState } from "react";
import { FONTS, readFont, readTextSize, saveTypography, TEXT_SIZES, type FontChoice, type TextSize } from "../design/typography";

/** Text size and typeface: applied at once, everywhere, and remembered on this device. */
export function TypographySettings() {
  const [size, setSize] = useState<TextSize>(readTextSize);
  const [font, setFont] = useState<FontChoice>(readFont);
  const change = (nextSize: TextSize, nextFont: FontChoice) => {
    setSize(nextSize);
    setFont(nextFont);
    saveTypography(nextSize, nextFont);
  };
  return <>
    <label className="setting-row"><span><strong>Text size</strong><small>Scales every label, message and note.</small></span>
      <select value={size} aria-label="Text size" onChange={(event) => change(event.target.value as TextSize, font)}>
        {TEXT_SIZES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
    <label className="setting-row"><span><strong>Typeface</strong><small>{FONTS.find((item) => item.id === font)?.detail}</small></span>
      <select value={font} aria-label="Typeface" onChange={(event) => change(size, event.target.value as FontChoice)}>
        {FONTS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
    <p className="type-preview">Evidence improves decisions. Ask a question and Gunther answers from your own sources, with the passages it used quoted beside it.</p>
  </>;
}
