import { Check, FileUp, Layers, Palette, Trash2 } from "lucide-react";
import type { MenuItem } from "../../contents/SectionMenu";
import { ArtifactMenu } from "../ArtifactMenu";
import { DECK_STYLES, type DeckStyle, type DeckTheme } from "../sections";
import { setBrandTheme, useBrandTheme } from "./brandStore";
import { importTemplateFile } from "./DeckThemeMenu";
import { STYLE_LABEL } from "./designStyles";

/**
 * The look of a designed deck: Light, Dark, Bold, and the imported template's colours and fonts when there is one.
 *
 * Picking a style recolours the deck at once (the title marker's `style:` is rewritten and saved; the slides' HTML uses only the
 * style's variables), then OutputsPage asks the backend to draw every slide again in that style (`restyleSlidesStream`),
 * which saves the next version. Only the latest pending style is applied after that runs. The template is only ever a recolour (`onTemplate`).
 * A studio deck with no style yet keeps the Theme menu (DeckThemeMenu) instead of this one: its look is the template's.
 */
export function DeckStyleMenu({ style, pendingStyle = null, theme, disabled, templateDisabled = disabled, onStyle, onTemplate, onNotice }: {
  style: DeckStyle;
  pendingStyle?: DeckStyle | null;
  theme: DeckTheme;
  disabled: boolean;
  templateDisabled?: boolean;
  onStyle: (style: DeckStyle) => void;
  /** The imported template is chosen (the marker's `theme: brand`), or removed (a built-in theme again). */
  onTemplate: (on: boolean) => void;
  onNotice: (text: string) => void;
}) {
  const brand = useBrandTheme();
  const withTemplate = theme === "brand" && !!brand;
  const items: MenuItem[] = [
    ...DECK_STYLES.map((name) => ({ label: `${STYLE_LABEL[name]}${pendingStyle === name ? " (next)" : ""}`, ...(!withTemplate && style === name ? { icon: Check } : {}), onSelect: () => onStyle(name) })),
    ...(brand ? [{ label: brand.name, icon: withTemplate ? Check : Layers, disabled: templateDisabled, onSelect: () => onTemplate(true) }] : []),
    { label: brand ? "Replace template…" : "Import template…", icon: FileUp, divider: true, disabled: templateDisabled, onSelect: () => void importTemplateFile(onNotice).then((worked) => { if (worked) onTemplate(true); }) },
    ...(brand ? [{ label: "Remove template", icon: Trash2, disabled: templateDisabled, onSelect: () => { setBrandTheme(null); if (withTemplate) onTemplate(false); } }] : []),
  ];
  return <ArtifactMenu label="Deck style" triggerLabel="Deck style" triggerClass="gx-btn gx-btn-quiet gx-btn-sm" trigger={<><Palette size={13} />Style</>} items={disabled ? items.map((item) => ({ ...item, disabled: true })) : items} />;
}
