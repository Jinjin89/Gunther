/** Shared, bounded measurements in stage pixels, after the real renderer has settled. */
export function measureSlide(face: HTMLElement, use: "talk" | "read") {
  const stage = face.getBoundingClientRect();
  const factor = stage.width / 1280 || 1;
  const rect = (element: Element) => {
    const box = element.getBoundingClientRect();
    return { x: (box.x - stage.x) / factor, y: (box.y - stage.y) / factor, width: box.width / factor, height: box.height / factor };
  };
  const visible = (element: HTMLElement) => {
    const hidden = element.closest(".outputs-sr-only,[aria-hidden='true']");
    return !(hidden && face.contains(hidden)) && getComputedStyle(element).visibility !== "hidden" && element.getBoundingClientRect().height > 0;
  };
  // The deck design (v2) scales its whole canvas to fit; type sizes are what is seen on the stage.
  const deck = face.classList.contains("dk-face");
  const scale = deck ? parseFloat(face.querySelector<HTMLElement>(".dk-canvas")?.style.getPropertyValue("--dk-fit") ?? "") || 1 : 1;
  const texts = [...face.querySelectorAll<HTMLElement>("h1,h2,h3,p,li,th,td,figcaption,svg text")].filter(element => element.textContent?.trim() && visible(element) && !(deck && element.closest(".dk-foot")));
  const typography = texts.map(element => {
    const style = getComputedStyle(element);
    const role = (deck ? /^H[12]$/ : /^H[123]$/).test(element.tagName) ? "heading" : element.closest(".outputs-s-after,.outputs-s-measure") || (deck && element.closest("figcaption,.dk-note")) ? "caption" : element.closest("svg") ? "label" : "body";
    // These are stage-pixel floors, not a penalty for every internal fit below one.
    // Reading pages allow smaller body type; captions and plot labels have their own floor.
    const cjk = /[\u3400-\u9fff]/.test(element.textContent ?? "");
    const minimum = deck ? (role === "heading" ? 20 : role === "body" ? 18 : 14) : role === "heading" ? 28 : role === "caption" || role === "label" ? 16 : use === "read" ? (cjk ? 20 : 18) : (cjk ? 24 : 22);
    const size = parseFloat(style.fontSize) * scale;
    return { role, size, minimum, family: style.fontFamily, blockId: element.closest<HTMLElement>("[data-block-id]")?.dataset.blockId ?? null, bounds: rect(element), text: element.textContent?.slice(0,160) ?? "" };
  });
  const blocks = [...face.querySelectorAll<HTMLElement>("[data-block-id]")].map(element => ({ id: element.dataset.blockId!, bounds: rect(element) }));
  const overlaps: Array<{ blocks: string[]; area: number }> = [];
  for (let a = 0; a < blocks.length; a++) for (let b = a+1; b < blocks.length; b++) {
    const left = blocks[a]!, right = blocks[b]!;
    if (left.id === right.id) continue;
    const width = Math.min(left.bounds.x+left.bounds.width,right.bounds.x+right.bounds.width)-Math.max(left.bounds.x,right.bounds.x);
    const height = Math.min(left.bounds.y+left.bounds.height,right.bounds.y+right.bounds.height)-Math.max(left.bounds.y,right.bounds.y);
    if (width > 2 && height > 2) overlaps.push({ blocks: [left.id,right.id], area: width*height });
  }
  let textOverflow = false;
  for (const element of texts) for (const child of element.childNodes) {
    if (child.nodeType !== Node.TEXT_NODE || !child.textContent?.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(child);
    for (const box of range.getClientRects()) {
      if (box.left < stage.left-2 || box.right > stage.right+2 || box.top < stage.top-2 || box.bottom > stage.bottom+2) textOverflow = true;
    }
  }
  return {
    fit: deck ? scale : parseFloat(face.style.getPropertyValue("--outputs-fit")) || 1,
    overflowWhy: face.dataset.overflowWhy ?? null,
    overflow: face.parentElement?.dataset.overflow === "true" || textOverflow,
    drawn: (deck ? face.dataset.layout : face.dataset.drawn) ?? "markdown", sceneIssue: face.dataset.sceneIssue || face.parentElement?.dataset.sceneIssue || null,
    bounds: { width: stage.width/factor, height: stage.height/factor }, blockBounds: blocks, typography, overlaps,
    smallText: typography.filter(text => text.size < text.minimum - 0.5),
    fonts: [...new Set(typography.map(text => text.family))],
    images: [...face.querySelectorAll("img")].map(image => ({ src: new URL(image.src).pathname, loaded: image.complete && image.naturalWidth > 0, width: image.naturalWidth, height: image.naturalHeight })),
    assetFailures: face.querySelectorAll(".outputs-s-missing,.outputs-composed-image-missing").length,
    pending: face.querySelectorAll('[data-graphic-fallback="pending"]').length,
  };
}

export async function waitForSlides(selector: string, count: number, timeout: number) {
  const deadline = performance.now() + timeout;
  let previous = "", stable = 0;
  while (performance.now() < deadline) {
    const faces = [...document.querySelectorAll<HTMLElement>(selector)];
    if (faces.length === count && document.fonts.status === "loaded") {
      await Promise.all(faces.flatMap(face => [...face.querySelectorAll("img")].map(image => image.decode().catch(() => undefined))));
      const pending = faces.some(face => face.dataset.layout === "composed" && face.dataset.sceneReady !== "true" || face.querySelector('[data-graphic-fallback="pending"]'));
      const signature = JSON.stringify(faces.map(face => [face.style.getPropertyValue("--outputs-fit"), face.dataset.drawn, face.innerText, [...face.querySelectorAll("img")].map(image => [image.complete, image.naturalWidth]), face.getBoundingClientRect().height]));
      stable = !pending && signature === previous ? stable + 1 : 0;
      previous = signature;
      if (stable >= 4) return;
    }
    await new Promise<void>(resolve => setTimeout(resolve, 80));
  }
  throw new Error("Slides did not finish fonts, images, scene validation and fitting before the capture timeout.");
}
