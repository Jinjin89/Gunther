import type { Link, PhrasingContent, Root } from "mdast";
import { textCitationMarks } from "../../evidence/citationMarks";
import { mapMarkdown } from "./mapMarkdown";
import { sourceForPositions } from "./plugins";

function citationLink(url: string, label: string): Link {
  return { type: "link", url, data: { hProperties: { dataCitation: true } }, children: [{ type: "text", value: label }] };
}

// Straight quotes can close an attached quotation, but a space before one may introduce a new quote.
const citationTail = /^(?:[ \t]+(?!["']))?([\p{Pe}\p{Pf}\p{Terminal_Punctuation}"']+)/u;

export function remarkCitations({ numbers, hide, tight }: { numbers: readonly number[]; hide: boolean; tight: boolean }) {
  return (tree: Root, file: { value: unknown }) => {
    const eligible = new Set(numbers);
    const source = sourceForPositions(file.value);
    let noteIndex = 0;
    mapMarkdown(tree, (node) => {
      // Markdown links already own their labels.
      if (node.type === "link" || node.type === "linkReference") return node;
      if (node.type !== "text") return;
      const marks = textCitationMarks(node, source);
      if (!marks.length) return node;

      const { value } = node;
      const parts: PhrasingContent[] = [];
      const text = (value: string) => { if (value) parts.push({ type: "text", value }); };
      let cursor = 0;
      for (let index = 0; index < marks.length; index++) {
        const mark = marks[index]!;
        const before = value.slice(cursor, mark.start);
        text(hide ? before.replace(/\s$/, "") : tight ? before.replace(/[ \t]+$/, "\u00a0") : before);
        cursor = mark.end;
        if (hide) continue;

        let link: Link | undefined;
        const [number] = mark.numbers;
        if (mark.kind === "unverified") {
          link = citationLink("#unverified", "?");
        } else if (number && mark.numbers.length === 1) {
          if (mark.kind === "p") {
            const note = noteIndex++;
            if (eligible.has(Number(number))) link = citationLink(`#partly-${number}-${note}`, number);
          } else if (mark.kind === "cite") {
            if (eligible.has(Number(number))) link = citationLink(`#cite-${number}`, number);
          } else {
            const sources = new Set<number>();
            let last = index;
            for (;;) {
              const current = marks[last]!;
              const cited = Number(current.numbers[0]);
              if (eligible.has(cited)) sources.add(cited);
              const next = marks[last + 1];
              if (next?.kind !== mark.kind || next.numbers.length !== 1 || !/^[ \t]*$/.test(value.slice(current.end, next.start))) break;
              last++;
            }
            index = last;
            cursor = marks[last]!.end;
            if (sources.size) {
              const inferred = mark.kind === "i";
              link = citationLink(`#${inferred ? "infer" : "disputed"}-${[...sources].join("-")}`, inferred ? "inference" : "disputed");
            }
          }
        }
        if (link) {
          const tail = citationTail.exec(value.slice(cursor));
          if (tail?.[1]) {
            link.data = { hProperties: { ...link.data?.hProperties, dataCitationTail: tail[1] } };
            cursor += tail[0].length;
          }
          parts.push(link);
        }
        else {
          const literal = value.slice(mark.start, cursor);
          text(tight && cursor > mark.end ? literal.replace(/[ \t]+/g, "\u00a0") : literal);
        }
      }
      text(value.slice(cursor));
      return parts;
    });
  };
}
