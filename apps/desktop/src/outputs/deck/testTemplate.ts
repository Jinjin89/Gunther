/** A small PowerPoint template with its own colours, fonts and a logo, made for tests and the gallery (never shipped as a feature). */
import JSZip from "jszip";
import PptxGenJS from "pptxgenjs";

const LOGO_PNG = "iVBORw0KGgoAAAANSUhEUgAAASwAAABkCAYAAAA8AQ3AAAACOElEQVR42u3dwU0CURSF4XkT2qEGFywtQErEAlyysAYKwq0m6gxEeffM+741iQTDn3NNkDbd4HTYXyeAP3Q8X9raxy4+UKSAKvGaxQqoYqk5TaiAlLU1ixWQsrZmsQJSojV7SYAUs3UFpKysWayAlGg5CYG8kxCguuYcBCwsAMECBAtAsAAECxAsAMECECxAsAAEC0CwAMECqGCX+KRf3t5XP/b1+clvGTYi5r813BIp8QLBioyUeIFgxYZKuCBXuT+6PzJWPX4esJFg9YqHaIGTMDIYTkSwsGLWjbUFghUVB9ECwYqKgmiBYAFkBStlvVhZMHiw0iIgWjBosFLf/KIFA5+EAKWDlb5SrCzob+clEEK4R49PhTgJASfhFleENQQDBAtAsADBckY5C8HCAhAsQLAABAtAsADBAhAsAMECBAtAsAC6B2tr36Tsm6HBwgIQLECwhjyjnINgYQGs4ksorDawsLb2xhQWcBIC1AxW6kqxrmDQhZX25hcrGPwkTImAWIFgAWQFq/p6sa5AsCKiIFYgWBFxECuoq50O+2uVJ9PzW5WFCiysiGiIFQhWRDzECpyE5U9EoQLBKh0vkQLBKh0vkQLBAng4H80BBAtAsADBAhAsAMECBAtAsAAECxAsAMECuC9Yx/OleRmA6o7nS7OwACchwL8Ey1kIVD8Hvyws0QIqx8pJCOSdhFYWUH1dfbuwRAuoGKtpmqZf4+T/vQMVQvXjwrK2gIqxWlxYFhfQO1KffQAIELar66EbAAAAAABJRU5ErkJggg==";

export const TEST_TEMPLATE = { name: "Aster Brand", background: "#fff8ee", text: "#1b2a41", accent: "#a8432d", accent2: "#2f6f73", headingFont: "Georgia", bodyFont: "Arial" } as const;

export async function makeTestTemplate(): Promise<Uint8Array> {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  pptx.defineSlideMaster({
    title: "ASTER", background: { color: "FFF8EE" },
    objects: [{ image: { x: 11.3, y: 0.15, w: 1.5, h: 0.5, data: `image/png;base64,${LOGO_PNG}` } }],
  });
  pptx.addSlide({ masterName: "ASTER" }).addText("Template", { x: 1, y: 1, w: 5, h: 1 });
  const zip = await JSZip.loadAsync(await pptx.write({ outputType: "arraybuffer" }) as ArrayBuffer);
  const file = Object.keys(zip.files).find((name) => /^ppt\/theme\/theme1\.xml$/.test(name))!;
  let xml = await zip.file(file)!.async("string");
  const set = (tag: string, value: string) => { xml = xml.replace(new RegExp(`<a:${tag}>[\\s\\S]*?</a:${tag}>`), `<a:${tag}><a:srgbClr val="${value}"/></a:${tag}>`); };
  set("dk1", "1B2A41"); set("lt1", "FFF8EE"); set("dk2", "324A5F"); set("lt2", "F0E6D6");
  set("accent1", "A8432D"); set("accent2", "2F6F73"); set("accent3", "E0A458"); set("accent4", "6B7A8F"); set("accent5", "8F5E7A"); set("accent6", "4F772D");
  xml = xml.replace(/(<a:majorFont>\s*<a:latin typeface=")[^"]*/, "$1Georgia").replace(/(<a:minorFont>\s*<a:latin typeface=")[^"]*/, "$1Arial");
  xml = xml.replace(/(<a:theme\b[^>]*\bname=")[^"]*/, "$1Aster Brand");
  zip.file(file, xml);
  return await zip.generateAsync({ type: "uint8array" });
}
