import { withoutMarks } from "../pages/AnswerBody";
import { splitDocument } from "./sections";

type CardHeading = { position: number; question: string };
export type StudyCard = CardHeading & (
  { kind: "flashcard"; answer: string } |
  { kind: "quiz"; options: string[]; correct: number; why: string }
);

const QUIZ_PREFIX = "(quiz)";
const OPTION = /^- \[([ x])\][ \t]+(\S.*)$/;

/** Read the card Markdown grammar used by the backend. Positions refer to the saved document. */
export function readCards(content: string): StudyCard[] {
  const cards: StudyCard[] = [];
  for (const [position, unit] of splitDocument(content, "cards").sections.entries()) {
    const [heading = "", ...body] = unit.split("\n");
    const title = heading.slice(3).trim();
    const quiz = title === QUIZ_PREFIX || title.startsWith(`${QUIZ_PREFIX} `);
    const question = quiz ? title.slice(QUIZ_PREFIX.length).trim() : title;
    if (!withoutMarks(question).trim()) continue;
    if (!quiz) {
      const answer = body.join("\n").trim();
      if (withoutMarks(answer).trim()) cards.push({ position, kind: "flashcard", question, answer });
      continue;
    }
    const lines = body.map((line) => line.trim()).filter(Boolean);
    const whyAt = lines.findIndex((line) => line.startsWith("Why:"));
    if (whyAt < 0) continue;
    const choices = lines.slice(0, whyAt).map((line) => OPTION.exec(line));
    if (!choices.every((choice) => choice !== null)) continue;
    const correct = choices.findIndex((choice) => choice[1] === "x");
    if (correct < 0 || choices.some((choice, index) => index !== correct && choice[1] === "x")) continue;
    const [first = "", ...rest] = lines.slice(whyAt);
    const why = [first.slice("Why:".length), ...rest].join(" ").trim();
    if (!withoutMarks(why).trim() || rest.some((line) => OPTION.test(line) || line.startsWith("Why:"))) continue;
    cards.push({ position, kind: "quiz", question, options: choices.map((choice) => choice[2]!), correct, why });
  }
  return cards;
}
