import { Check, FileUp, Layers, Trash2 } from "lucide-react";
import type { MenuItem } from "../../contents/SectionMenu";
import { ArtifactMenu } from "../ArtifactMenu";
import { DECK_THEMES, type DeckTheme } from "../sections";
import { readTemplate } from "./brand";
import { setBrandTheme } from "./brandStore";
import { useBrandTheme } from "./brandStore";
import { DECK_THEME_TOKENS } from "./theme";

/** Pick a file from the computer: the browser needs a click on an input, which is made and thrown away here. */
function chooseFile(accept: string): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.onchange = () => resolve(input.files?.[0] ?? null);
    input.oncancel = () => resolve(null);
    input.click();
  });
}

/**
 * The theme of a studio deck: Paper, Ink, Editorial, and the imported template's own look when there is one.
 * "Import template…" reads a .pptx/.potx and keeps its colours, fonts and logo (one at a time, on this computer).
 */
export function DeckThemeMenu({ value, disabled, onSelect, onNotice }: {
  value: DeckTheme;
  disabled: boolean;
  onSelect: (theme: DeckTheme) => void;
  onNotice: (text: string) => void;
}) {
  const brand = useBrandTheme();
  const current = value === "brand" && !brand ? "paper" : value;
  const importTemplate = async () => {
    const file = await chooseFile(".pptx,.potx,application/vnd.openxmlformats-officedocument.presentationml.presentation");
    if (!file) return;
    try {
      const { brand: imported, notes } = await readTemplate(await file.arrayBuffer(), file.name);
      setBrandTheme(imported);
      onNotice([`Template “${imported.name}” imported.`, ...notes].join(" "));
      onSelect("brand");
    } catch (reason) {
      onNotice(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const items: MenuItem[] = [
    ...DECK_THEMES.filter((name) => name !== "brand").map((name) => ({ label: DECK_THEME_TOKENS[name as Exclude<DeckTheme, "brand">].label, ...(current === name ? { icon: Check } : {}), onSelect: () => onSelect(name) })),
    ...(brand ? [{ label: brand.name, icon: current === "brand" ? Check : Layers, onSelect: () => onSelect("brand") }] : []),
    { label: brand ? "Replace template…" : "Import template…", icon: FileUp, divider: true, onSelect: () => void importTemplate() },
    ...(brand ? [{ label: "Remove template", icon: Trash2, onSelect: () => { setBrandTheme(null); if (value === "brand") onSelect("paper"); } }] : []),
  ];
  return <ArtifactMenu label="Deck theme" triggerLabel="Deck theme" triggerClass="gx-btn gx-btn-quiet gx-btn-sm" trigger={<><Layers size={13} />Theme</>} items={disabled ? items.map((item) => ({ ...item, disabled: true })) : items} />;
}
