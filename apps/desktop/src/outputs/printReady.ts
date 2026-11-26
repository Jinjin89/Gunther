function afterPaint() {
  return new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

function slidesAreReady(paper: HTMLElement) {
  const deck = paper.querySelector<HTMLElement>(".outputs-print-deck");
  const page = ".outputs-print-slide:not(.outputs-print-slide-sources)";
  if (!deck || deck.querySelectorAll(page).length !== Number(deck.dataset.slideCount)) return false;
  return [...deck.querySelectorAll<HTMLElement>("[data-designed='true']")].every((host) => {
    if (host.dataset.ready !== "true") return false;
    if (host.dataset.expectsChart !== "true") return true;
    const plot = host.shadowRoot?.querySelector("gunther-chart .dk-plot svg");
    return Boolean(plot && plot.getBoundingClientRect().width > 0);
  });
}

async function loadPrintImage(image: HTMLImageElement) {
  // The preparation layout is off screen, where the browser can defer lazy images indefinitely.
  image.loading = "eager";
  try { await image.decode(); }
  catch { throw new Error(image.alt ? `Could not load the print image “${image.alt}”.` : "Could not load an image for printing."); }
}

/** Lay out the verified version off screen before the print dialog. The caller releases the layout after printing. */
export async function prepareForPrint(artifactId: string, timeout = 8000): Promise<() => void> {
  const paper = document.getElementById("print-root");
  if (!paper || paper.dataset.artifactId !== artifactId) throw new Error("Open the saved version to print it.");
  const root = document.documentElement;
  if (root.dataset.printPrep) throw new Error("Print preparation is already running.");
  root.dataset.printPrep = "1";
  let active = true;
  const done = () => {
    if (!active) return;
    active = false;
    delete root.dataset.printPrep;
  };
  const settle = async () => {
    await afterPaint();
    if (paper.dataset.kind !== "slides") {
      await Promise.all([...paper.querySelectorAll("img")].map(loadPrintImage));
    }
    while (active) {
      if (paper.querySelector('[data-equation-state="failed"]')) throw new Error("Could not render an equation for printing.");
      if (!paper.querySelector('[data-equation-state="pending"]') && (paper.dataset.kind !== "slides" || slidesAreReady(paper))) break;
      await new Promise<void>((resolve) => setTimeout(resolve, 60));
    }
    await document.fonts?.ready;
    await afterPaint();
    if (!paper.isConnected || paper.dataset.artifactId !== artifactId) throw new Error("The open version changed while preparing to print.");
  };

  let alarm: number | undefined;
  try {
    await Promise.race([settle(), new Promise<never>((_, reject) => {
      alarm = window.setTimeout(() => reject(new Error("The output did not finish preparing for print.")), timeout);
    })]);
    return done;
  } catch (reason) {
    done();
    throw reason;
  } finally {
    window.clearTimeout(alarm);
  }
}
