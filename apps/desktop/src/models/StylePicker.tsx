import { ANSWER_STYLES, useAnswerStyle } from "./askModel";

/** How the next answer is worded: a small menu beside the model chips. */
export function StylePicker({ disabled = false }: { disabled?: boolean }) {
  const [style, choose] = useAnswerStyle();
  const current = ANSWER_STYLES.find((item) => item.id === style) ?? ANSWER_STYLES[0];
  return <label className="gx-style-picker" title={`Answer style: ${current.hint}. Every style cites its sources.`}>
    <span className="gx-sr-only">Answer style</span>
    <select value={current.id} disabled={disabled} onChange={(event) => choose(event.target.value)} aria-label="Answer style">
      {ANSWER_STYLES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select>
  </label>;
}
