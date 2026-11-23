/** Strip source number and unverified markers while keeping the surrounding text. */
export function withoutMarks(text: string): string {
  return text.replace(/\s?\[(?:(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*|\?)\]/g, "");
}
