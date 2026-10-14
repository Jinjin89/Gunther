import { ANSWER_STYLES, useAnswerStyle } from "./askModel";

/** The device's answer preference, edited in Settings and used by every Ask surface. */
export function AnswerStyleSetting() {
  const [style, choose] = useAnswerStyle();
  const current = ANSWER_STYLES.find((item) => item.id === style) ?? ANSWER_STYLES[0];
  return <label className="setting-row">
    <span><strong>Answer style</strong><small>{current.hint}. Applies to Ask in Home and every library; every style cites its sources.</small></span>
    <select value={current.id} onChange={(event) => choose(event.target.value)} aria-label="Answer style">
      {ANSWER_STYLES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select>
  </label>;
}
