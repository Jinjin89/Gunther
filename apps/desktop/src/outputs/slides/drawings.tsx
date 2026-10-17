import { BarChartSlide } from "./BarChartSlide";
import { BigNumberSlide } from "./BigNumberSlide";
import { CardsSlide } from "./CardsSlide";
import { CompareSlide, TwoColumnSlide } from "./CompareSlide";
import { FigureSlide } from "./FigureSlide";
import { FlowSlide } from "./FlowSlide";
import { LineChartSlide } from "./LineChartSlide";
import type { SlideShape } from "./parseSlide";
import { QuoteSlide } from "./QuoteSlide";
import { TakeawaysSlide } from "./TakeawaysSlide";
import { TextSlide } from "./TextSlide";
import { TimelineSlide } from "./TimelineSlide";
import { TitleSlide } from "./TitleSlide";
import "./slides.css";

/** A slide drawn in the layout it was read as: one component for each. */
export function Drawing({ shape }: { shape: SlideShape }) {
  switch (shape.layout) {
    case "title": return <TitleSlide shape={shape} />;
    case "big_number": return <BigNumberSlide shape={shape} />;
    case "compare": return <CompareSlide shape={shape} />;
    case "two_column": return <TwoColumnSlide shape={shape} />;
    case "flow": return <FlowSlide shape={shape} />;
    case "timeline": return <TimelineSlide shape={shape} />;
    case "bar_chart": return <BarChartSlide shape={shape} />;
    case "line_chart": return <LineChartSlide shape={shape} />;
    case "cards": return <CardsSlide shape={shape} />;
    case "quote": return <QuoteSlide shape={shape} />;
    case "figure": return <FigureSlide shape={shape} />;
    case "text": return <TextSlide shape={shape} />;
    case "takeaways": return <TakeawaysSlide shape={shape} />;
  }
}
