import { withoutMarks } from "../components/evidence/citationMarks";
import { splitDocument } from "./sections";

type CardHeading = { position: number; question: string };
export type StudyCard = CardHeading & (
  { kind: "flashcard"; answer: string } |
  { kind: "quiz"; options: string[]; correct: number; why: string }
);

const QUIZ_PREFIX = "(quiz)";
const OPTION = /^- \[([ x])\][ \t]+(\S.*)$/;

function readCard(unit: string, position: number): StudyCard | string {
  const [heading = "", ...body] = unit.split("\n");
  const title = heading.slice(3).trim();
  const quiz = title === QUIZ_PREFIX || title.startsWith(`${QUIZ_PREFIX} `);
  const question = quiz ? title.slice(QUIZ_PREFIX.length).trim() : title;
  if (!withoutMarks(question).trim()) return "The question is missing.";
  if (!quiz) {
    const answer = body.join("\n").trim();
    return withoutMarks(answer).trim() ? { position, kind: "flashcard", question, answer } : "The answer is missing.";
  }

  const whyAt = body.findIndex((line) => line.trimStart().startsWith("Why:"));
  if (whyAt < 0) return "The explanation is missing.";
  const choices = body.slice(0, whyAt).map((line) => line.trim()).filter(Boolean).map((line) => OPTION.exec(line));
  if (!choices.every((choice) => choice !== null)) return "An option is unreadable.";
  const correct = choices.findIndex((choice) => choice[1] === "x");
  if (correct < 0 || choices.some((choice, index) => index !== correct && choice[1] === "x")) {
    return "The quiz needs one marked correct answer.";
  }
  const [first = "", ...rest] = body.slice(whyAt);
  const why = [first.trimStart().slice("Why:".length).trimStart(), ...rest].join("\n");
  if (!withoutMarks(why).trim()) return "The explanation is missing.";
  if (rest.some((line) => OPTION.test(line.trim()) || line.trimStart().startsWith("Why:"))) {
    return "The explanation must follow all of the options.";
  }
  return { position, kind: "quiz", question, options: choices.map((choice) => choice[2]!), correct, why };
}

/** Decode the saved card grammar and keep positions even when a card is unreadable. */
export function readCards(content: string) {
  const cards: StudyCard[] = [];
  const skipped: { position: number; reason: string }[] = [];
  for (const [position, unit] of splitDocument(content, "cards").sections.entries()) {
    const card = readCard(unit, position);
    if (typeof card === "string") skipped.push({ position, reason: card });
    else cards.push(card);
  }
  return { cards, skipped };
}
