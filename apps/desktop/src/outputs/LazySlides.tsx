import { type ComponentProps, lazy, Suspense } from "react";
import { LoaderCircle } from "lucide-react";

// reveal.js is only fetched when a deck is opened.
const SlidesView = lazy(() => import("./SlidesView").then((module) => ({ default: module.SlidesView })));

/** The slide viewer, once it has loaded. */
export function LazySlidesView(props: ComponentProps<typeof SlidesView>) {
  return <Suspense fallback={<div className="outputs-picker-wait"><LoaderCircle className="spin" size={15} />Opening the deck…</div>}>
    <SlidesView {...props} />
  </Suspense>;
}
