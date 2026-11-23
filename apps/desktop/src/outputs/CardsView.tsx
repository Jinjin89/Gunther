import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { ChevronLeft, ChevronRight, MessageSquarePlus } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CitationDetails } from "../components/evidence/CitationButton";
import { AnswerBody, citationNumbers, withoutMarks } from "../pages/AnswerBody";
import { SectionStatus } from "./ReportView";
import { readCards, type StudyCard } from "./cards";

type CardReaderProps = {
  artifact: Artifact;
  onCite?: ((citation: ConversationCitation, index: number) => void) | undefined;
  onAsk?: ((index: number) => void) | undefined;
  changed?: readonly number[] | undefined;
};

function cardCitations(artifact: Artifact, onCite: CardReaderProps["onCite"]) {
  return {
    numbers: citationNumbers(artifact.citations),
    onCitation: onCite ? (index: number) => {
      const citation = artifact.citations[index];
      if (citation) onCite(citation, index);
    } : undefined,
  };
}

function CardAnswer({ card, selected, citations }: {
  card: StudyCard;
  selected?: number | undefined;
  citations: ReturnType<typeof cardCitations>;
}) {
  if (card.kind === "flashcard") return <AnswerBody content={card.answer} {...citations} />;
  return <div className="cards-answer">
    {selected !== undefined && <p className="cards-feedback" role="status">{selected === card.correct ? "Correct" : "Incorrect"}</p>}
    <ol className="cards-options">
      {card.options.map((option, index) => <li key={index} className={index === card.correct ? "is-correct" : undefined}>
        <AnswerBody content={option} {...citations} />
        {index === card.correct ? <strong>Correct answer</strong>
          : index === selected ? <span>Your answer</span> : null}
      </li>)}
    </ol>
    <AnswerBody content={`**Why:** ${card.why}`} {...citations} />
  </div>;
}

function CardTools({ position, onAsk, changed }: { position: number } & Pick<CardReaderProps, "onAsk" | "changed">) {
  if (!onAsk && !changed?.includes(position)) return null;
  return <div className="outputs-section-tools">
    {changed?.includes(position) && <em className="outputs-changed">Changed</em>}
    {onAsk && <button type="button" className="outputs-ask-section" onClick={() => onAsk(position)}>
      <MessageSquarePlus size={12} aria-hidden="true" /> Refine this card
    </button>}
  </div>;
}

/** The answer sheet, also used for print with static source numbers. */
export function CardsList({ artifact, onCite, onAsk, changed }: CardReaderProps) {
  const cards = useMemo(() => readCards(artifact.content), [artifact.content]);
  const citations = cardCitations(artifact, onCite);
  if (!cards.length) return <p>No readable cards.</p>;
  return <CitationDetails.Provider value={artifact.citations}><ol className="cards-list" aria-label="All cards">
    {cards.map((card) => <li key={artifact.outline[card.position]?.id ?? card.position} className="cards-list-item">
      <CardTools position={card.position} onAsk={onAsk} changed={changed} />
      <AnswerBody content={`## ${card.question}`} {...citations} />
      <CardAnswer card={card} citations={citations} />
      {onCite && <SectionStatus info={artifact.sections[card.position]} />}
    </li>)}
  </ol></CitationDetails.Provider>;
}

export function CardsView({ artifact, onCite, onAsk, changed, onPart, goTo }: CardReaderProps & {
  onPart?: ((index: number | null) => void) | undefined;
  goTo?: { index: number; nonce: number } | null | undefined;
}) {
  const cards = useMemo(() => readCards(artifact.content), [artifact.content]);
  const [page, setPage] = useState(0);
  const [list, setList] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const reader = useRef<HTMLElement>(null);
  const index = Math.min(page, cards.length - 1);
  const card = cards[index];
  const position = card?.position ?? null;

  useEffect(() => {
    if (!goTo) return;
    const next = cards.findIndex((item) => item.position === goTo.index);
    if (next < 0) return;
    setPage(next);
    setFlipped(false);
    setList(false);
  }, [cards, goTo]);
  useEffect(() => { onPart?.(list ? null : position); }, [list, onPart, position]);

  if (!card) return <p>No readable cards.</p>;
  const citations = cardCitations(artifact, onCite);
  const quizzes = cards.filter((item) => item.kind === "quiz");
  const answered = quizzes.filter((item) => answers[item.position] !== undefined);
  const correct = answered.filter((item) => answers[item.position] === item.correct).length;
  const selected = answers[card.position];
  const revealed = card.kind === "flashcard" ? flipped : selected !== undefined;
  const move = (next: number) => {
    if (next < 0 || next >= cards.length) return;
    setPage(next);
    setFlipped(false);
  };
  const keys = (event: KeyboardEvent<HTMLElement>) => {
    if (list || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      event.currentTarget.focus();
      move(index + (event.key === "ArrowLeft" ? -1 : 1));
    } else if (event.key === " " && event.target === event.currentTarget && card.kind === "flashcard") {
      event.preventDefault();
      setFlipped((current) => !current);
    }
  };

  return <CitationDetails.Provider value={artifact.citations}><section ref={reader} className="cards-reader" tabIndex={0} aria-label="Card reader" onKeyDown={keys}>
    <div className="cards-toolbar">
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-pressed={list} onClick={() => setList(!list)}>List view</button>
      {quizzes.length > 0 && <div className="cards-score">
        {answered.length > 0 && <span role="status">{correct} of {answered.length} correct</span>}
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!answered.length}
          onClick={() => { setAnswers({}); setList(false); move(0); }}>Start again</button>
      </div>}
    </div>
    {list ? <CardsList artifact={artifact} onCite={onCite} onAsk={onAsk} changed={changed} /> : <>
      <div className="cards-practice">
        <CardTools position={card.position} onAsk={onAsk} changed={changed} />
        <div className="cards-question">
          <AnswerBody content={`## ${revealed ? card.question : withoutMarks(card.question)}`} {...citations} />
          {card.kind === "flashcard" && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm cards-flip"
            aria-expanded={flipped} onClick={() => setFlipped(!flipped)}>{flipped ? "Hide answer" : "Show answer"}</button>}
        </div>
        {card.kind === "quiz" && !revealed && <ol className="cards-options" aria-label="Choices">
          {card.options.map((option, choice) => <li key={choice}><button type="button" className="cards-option"
            onClick={() => {
              setAnswers((current) => ({ ...current, [card.position]: choice }));
              reader.current?.focus();
            }}>{withoutMarks(option)}</button></li>)}
        </ol>}
        {revealed && <>
          <CardAnswer card={card} selected={selected} citations={citations} />
          <SectionStatus info={artifact.sections[card.position]} />
        </>}
      </div>
      <nav className="cards-pagination" aria-label="Card navigation">
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={index === 0} onClick={() => move(index - 1)}><ChevronLeft size={14} aria-hidden="true" />Previous</button>
        <span aria-live="polite">{index + 1} / {cards.length}</span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={index >= cards.length - 1} onClick={() => move(index + 1)}>Next<ChevronRight size={14} aria-hidden="true" /></button>
      </nav>
    </>}
  </section></CitationDetails.Provider>;
}
