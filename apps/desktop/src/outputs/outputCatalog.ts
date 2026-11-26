import type { Artifact, OutputType } from "@gunther/contracts";
import { AudioLines, BookOpen, CalendarDays, CircleHelp, ClipboardList, FileText, Layers, ListChecks, Network, Presentation } from "lucide-react";

const icons = { AudioLines, BookOpen, CalendarDays, CircleHelp, ClipboardList, FileText, Layers, ListChecks, Network, Presentation };

export function outputIcon(type?: Pick<OutputType, "icon">) {
  return type && Object.hasOwn(icons, type.icon) ? icons[type.icon as keyof typeof icons] : FileText;
}

export function resolveOutputType(types: readonly OutputType[], { kind, style }: Pick<Artifact, "kind" | "style">) {
  if (!style) return undefined;
  return types.find((type) => type.shape === kind && type.id === style)
    ?? types.find((type) => type.shape === kind && type.legacyStyles?.includes(style));
}
