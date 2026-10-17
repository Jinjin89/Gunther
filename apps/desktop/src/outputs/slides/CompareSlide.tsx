import type { Align, TableShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

const alignOf = (align: Align | undefined) => (align ? { textAlign: align } : undefined);

/** A few objects in several respects: a grid with the respects in muted type down the side and each object's name in bold. */
export function CompareSlide({ shape }: { shape: TableShape }) {
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <table className="outputs-s-table">
      <thead>
        <tr>{shape.head.map((cell, at) => <th key={at} scope="col" style={alignOf(shape.align[at])}><Rich text={cell} /></th>)}</tr>
        <tr className="outputs-s-underline" aria-hidden="true"><td colSpan={shape.head.length} /></tr>
      </thead>
      <tbody>
        {shape.rows.map((row, at) => <tr key={at}>
          {row.map((cell, column) => column === 0
            ? <th key={column} scope="row" style={alignOf(shape.align[column])}><Rich text={cell} /></th>
            : <td key={column} style={alignOf(shape.align[column])}><Rich text={cell} /></td>)}
        </tr>)}
      </tbody>
    </table>
  </Frame>;
}

/** Two views of the same questions: two panels side by side, row for row. */
export function TwoColumnSlide({ shape }: { shape: TableShape }) {
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <table className="outputs-s-sides">
      <thead>
        <tr>{shape.head.map((cell, at) => <th key={at} scope="col"><Rich text={cell} /></th>)}</tr>
      </thead>
      <tbody>
        {shape.rows.map((row, at) => <tr key={at}>{row.map((cell, column) => <td key={column}><Rich text={cell} /></td>)}</tr>)}
      </tbody>
    </table>
  </Frame>;
}
