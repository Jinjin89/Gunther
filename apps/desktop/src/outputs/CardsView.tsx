import { Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { CitationDetails } from "../components/evidence/CitationButton";
import { AnswerBody, withoutMarks } from "../pages/AnswerBody";
import { readCards, type StudyCard } from "./cards";
import { OutputPartTools, SectionStatus, outputCitations, type OutputContentProps } from "./OutputParts";
import "./cards.css";

type CardSet = ReturnType<typeof readCards>;
type CardProps = Pick<OutputContentProps, "artifact" | "onAsk" | "changed" | "presentation"> & {
  citations: ReturnType<typeof outputCitations>;
};
type QuizCard = Extract<StudyCard, { kind: "quiz" }>;

function clickedText(event: MouseEvent<HTMLElement>) {
  if (event.defaultPrevented || !(event.target instanceof Element)
    || event.target.closest("a, button, input, select, textarea, summary, [contenteditable]")) return false;
  return !event.currentTarget.ownerDocument.getSelection()?.toString();
}

function QuizChoices({ card, citations, selected, onChoose }: Pick<CardProps, "citations"> & {
  card: QuizCard;
  selected: number | undefined;
  onChoose: ((index: number) => void) | undefined;
}) {
  const id = useId();
  return <ol className="cards-choices" type="A" role="list" aria-label="Choices">
    {card.options.map((option, index) => {
      const optionId = `${id}-${index}`;
      const correct = !onChoose && index === card.correct;
      const chosen = index === selected;
      const label = correct ? (chosen ? "Correct · your answer" : "Correct answer") : chosen ? "Your answer" : undefined;
      const Icon = correct ? Check : X;
      return <li key={index} className={correct ? "cards-choice is-correct" : "cards-choice"} onClick={onChoose && ((event) => {
        if (clickedText(event)) onChoose(index);
      })}>
        {onChoose && <button type="button" className="cards-select"
          aria-labelledby={withoutMarks(option).trim() ? optionId : undefined}
          aria-label={`Option ${index + 1}`} onClick={() => onChoose(index)} />}
        <span className="cards-letter" aria-hidden="true" />
        <div className="cards-option-text" id={optionId}>
          <AnswerBody content={option} {...citations} hideCitations={Boolean(onChoose)} inline />
        </div>
        {label && <strong className="cards-choice-result">
          <Icon size={14} aria-hidden="true" />{label}
        </strong>}
      </li>;
    })}
  </ol>;
}

function CardBody({ card, artifact, citations, onAsk, changed, presentation, revealed = true, selected, onToggle, onChoose }: CardProps & {
  card: StudyCard;
  revealed?: boolean;
  selected?: number | undefined;
  onToggle?: (() => void) | undefined;
  onChoose?: ((index: number) => void) | undefined;
}) {
  const answerId = useId();
  const canShowStatus = presentation !== "print";
  const tools = canShowStatus
    ? <OutputPartTools position={card.position} onAsk={onAsk} changed={changed} label="Refine this card" />
    : null;
  return <>
    <div className="cards-prompt" data-reveal={onToggle ? revealed : undefined} onClick={onToggle && ((event) => {
      if (clickedText(event)) onToggle();
    })}>
      <AnswerBody content={`## ${card.question}`} {...citations} hideCitations={!onToggle && !revealed} />
    </div>
    {onToggle && <div className="cards-reveal-bar">
      <button type="button" className="gx-btn gx-btn-primary cards-reveal" aria-expanded={revealed}
        aria-controls={answerId} onClick={onToggle}>{revealed ? "Hide answer" : "Show answer"}</button>
      {tools}
    </div>}
    {card.kind === "quiz" && <QuizChoices card={card} citations={citations} selected={selected} onChoose={onChoose} />}
    {(revealed || onToggle) && <div className="cards-response" id={onToggle ? answerId : undefined} data-revealed={revealed}>
      {revealed && <>
        {card.kind === "quiz" && selected !== undefined &&
          <p className="cards-feedback">{selected === card.correct ? "Correct" : "Incorrect"}</p>}
        {card.kind === "quiz" && <p className="cards-why"><strong>{"Why: "}</strong></p>}
        <AnswerBody content={card.kind === "flashcard" ? card.answer : card.why} {...citations} />
        {canShowStatus && <SectionStatus info={artifact.sections[card.position]} />}
      </>}
    </div>}
    {!onToggle && tools}
  </>;
}

function CardNotices({ cards, skipped }: CardSet) {
  return <>
    {cards.length === 0 && <p className="cards-notice">This version has no readable cards.</p>}
    {skipped.length > 0 && <ul className="cards-notice" aria-label="Skipped cards">
      {skipped.map(({ position, reason }) => <li key={position}>Card {position + 1} was skipped. {reason}</li>)}
    </ul>}
  </>;
}

function CardSheet({ cards, ...props }: CardProps & { cards: StudyCard[] }) {
  if (!cards.length) return null;
  return <ol className="cards-sheet" aria-label="All cards">
    {cards.map((card) => <li key={card.position} className="cards-list-item" value={card.position + 1} data-section={card.position}>
      <CardBody card={card} {...props} />
    </li>)}
  </ol>;
}

/** The answer sheet, also used for print with static source numbers. */
export function CardsList(props: OutputContentProps) {
  const { artifact } = props;
  const set = useMemo(() => readCards(artifact.content), [artifact.content]);
  return <CitationDetails.Provider value={artifact.citations}>
    <CardNotices {...set} />
    <CardSheet cards={set.cards} {...props} citations={outputCitations(props)} />
  </CitationDetails.Provider>;
}

const EMPTY_ATTEMPTS = { choices: {} as Record<number, number>, announcement: "" };

export function CardsView({ artifact, onCite, onAsk, changed, onPart, goTo, presentation }: OutputContentProps) {
  const set = useMemo(() => readCards(artifact.content), [artifact.content]);
  const { cards, skipped } = set;
  const [page, setPage] = useState<{ index: number } | null>(null);
  const [list, setList] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const [attempts, setAttempts] = useState(EMPTY_ATTEMPTS);
  const reader = useRef<HTMLElement>(null);
  const index = Math.min(page?.index ?? 0, cards.length - 1);
  const card = cards[index];
  const position = card?.position ?? null;
  const total = cards.length + skipped.length;

  const move = useCallback((next: number, focus = true) => {
    if (next < 0 || next >= cards.length) return;
    setPage({ index: next });
    setFlipped(false);
    setList(false);
    if (focus) reader.current?.focus({ preventScroll: true });
  }, [cards.length]);
  useEffect(() => {
    if (goTo) move(cards.findIndex((item) => item.position === goTo.index), false);
  }, [cards, goTo, move]);
  useLayoutEffect(() => {
    if (!page) return;
    const prompt = reader.current?.querySelector(".cards-turn .cards-prompt");
    const pane = prompt?.closest<HTMLElement>(".library-middle-page");
    if (!prompt || !pane) return;
    const viewport = pane.getBoundingClientRect();
    const bar = pane.querySelector(".library-middle-bar");
    const top = Math.max(viewport.top, bar?.getBoundingClientRect().bottom ?? viewport.top);
    const bounds = prompt.getBoundingClientRect();
    if (bounds.top < top || bounds.bottom > viewport.bottom) pane.scrollTop += bounds.top - top;
  }, [page]);
  useEffect(() => { onPart?.(list ? null : position); }, [list, onPart, position]);

  const cardProps = { artifact, onAsk, changed, presentation, citations: outputCitations({ artifact, onCite, presentation }) };
  let answered = 0;
  let correct = 0;
  for (const item of cards) {
    const choice = attempts.choices[item.position];
    if (item.kind !== "quiz" || choice === undefined) continue;
    answered += 1;
    if (choice === item.correct) correct += 1;
  }
  const selected = card?.kind === "quiz" ? attempts.choices[card.position] : undefined;
  const toggle = () => setFlipped((current) => !current);

  function choose(choice: number) {
    if (card?.kind !== "quiz") return;
    setAttempts((previous) => previous.choices[card.position] === undefined ? {
      choices: { ...previous.choices, [card.position]: choice },
      announcement: `Card ${card.position + 1}. ${choice === card.correct ? "Correct" : "Incorrect"}.`,
    } : previous);
    reader.current?.focus({ preventScroll: true });
  }

  function keys(event: KeyboardEvent<HTMLElement>) {
    if (!card || list || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const target = event.target;
      if (target instanceof HTMLElement && target !== event.currentTarget && target.scrollWidth > target.clientWidth) {
        const overflow = target.ownerDocument.defaultView?.getComputedStyle(target).overflowX;
        if (overflow === "auto" || overflow === "scroll") return;
      }
      event.preventDefault();
      move(index + (event.key === "ArrowLeft" ? -1 : 1));
    } else if (event.key === " " && event.target === event.currentTarget && card.kind === "flashcard") {
      event.preventDefault();
      toggle();
    }
  }

  const results = answered > 0 && <div className="cards-results">
    <span>{correct} of {answered} correct</span>
    <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => {
      setAttempts(EMPTY_ATTEMPTS);
      move(0);
    }}>Start again</button>
  </div>;

  return <CitationDetails.Provider value={artifact.citations}>
    <section ref={reader} className="cards-reader reader-track" data-track="structured" tabIndex={0} aria-label="Card reader" onKeyDown={keys}>
      {card && <header className="cards-session">
        <div className="cards-mode" role="group" aria-label="Study mode">
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" aria-pressed={!list} onClick={() => setList(false)}>Practice</button>
          <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" aria-pressed={list} onClick={() => setList(true)}>All cards</button>
        </div>
        <span className="cards-position" aria-live="polite">{card.position + 1} of {total}</span>
        <progress className="cards-progress" aria-hidden="true" value={card.position + 1} max={total} />
      </header>}
      <CardNotices {...set} />
      <div className="gx-sr-only" role="status">{attempts.announcement}</div>
      {list ? <>{results}<CardSheet cards={cards} {...cardProps} /></> : card && <>
        <div key={card.position} className="cards-turn" data-section={card.position} data-card-kind={card.kind}>
          <CardBody card={card} {...cardProps} revealed={card.kind === "flashcard" ? flipped : selected !== undefined}
            selected={selected} onToggle={card.kind === "flashcard" ? toggle : undefined}
            onChoose={card.kind === "quiz" && selected === undefined ? choose : undefined} />
        </div>
        <nav className="cards-nav" aria-label="Card navigation">
          <button type="button" className="gx-btn gx-btn-quiet" disabled={index === 0} onClick={() => move(index - 1)}>
            <ChevronLeft size={16} aria-hidden="true" />Previous
          </button>
          <button type="button" className="gx-btn gx-btn-quiet" disabled={index === cards.length - 1} onClick={() => move(index + 1)}>
            Next<ChevronRight size={16} aria-hidden="true" />
          </button>
        </nav>
        {results}
      </>}
    </section>
  </CitationDetails.Provider>;
}
