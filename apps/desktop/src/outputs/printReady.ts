/**
 * Before the browser prints a deck, its designed slides must have fonts and charts drawn. The print layout is hidden on screen,
 * where a chart has no size to be drawn at, so it is shown off-screen (`data-print-prep` on the page, see outputs.css) until every
 * designed slide says it is ready and every chart has its picture. Resolves when printing may start; `done` puts the page back.
 */
export async function prepareForPrint(timeout = 8000): Promise<() => void> {
  const root = document.documentElement;
  root.dataset.printPrep = "1";
  const done = () => { delete root.dataset.printPrep; };
  const deadline = performance.now() + timeout;
  const ready = () => {
    const hosts = [...document.querySelectorAll<HTMLElement>("#print-root [data-designed='true']")];
    return hosts.every((host) => {
      if (host.dataset.ready !== "true") return false;
      const chart = host.shadowRoot?.querySelector("gunther-chart");
      if (!chart) return true;
      const plot = chart.querySelector(".dk-plot svg, svg");
      return !!plot && plot.getBoundingClientRect().width > 0;
    });
  };
  await document.fonts?.ready;
  while (!ready() && performance.now() < deadline) await new Promise<void>((resolve) => setTimeout(resolve, 60));
  // Let the last frame draw.
  await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  return done;
}
