import type { CSSProperties } from "react";
import type { SlideTheme } from "../sections";

export const SLIDE_THEMES: Record<SlideTheme, {
  label: string;
  description: string;
  paper: string;
  surface: string;
  surfaceSoft: string;
  ink: string;
  muted: string;
  rule: string;
  axis: string;
  grey: string;
  accent: string;
  headingFont: "display" | "sans";
}> = {
  classic: { label: "Classic", description: "Clear and familiar", paper: "#ffffff", surface: "#f3f3f1", surfaceSoft: "#f9f9f8", ink: "#1a1a18", muted: "#5f5e5a", rule: "#dcdbd6", axis: "#777671", grey: "#949390", accent: "#2668b6", headingFont: "display" },
  briefing: { label: "Briefing", description: "Crisp and structured", paper: "#ffffff", surface: "#f1f4f7", surfaceSoft: "#f8fafc", ink: "#20211f", muted: "#5c5b56", rule: "#d5dce3", axis: "#777671", grey: "#949390", accent: "#315f91", headingFont: "sans" },
  editorial: { label: "Editorial", description: "Warm with serif headings", paper: "#fbf7ef", surface: "#f1eadd", surfaceSoft: "#f7f2e8", ink: "#29251f", muted: "#62594d", rule: "#ddd3c3", axis: "#827563", grey: "#938878", accent: "#825c36", headingFont: "display" },
  keynote: { label: "Keynote", description: "Dark and high contrast", paper: "#171a20", surface: "#242a34", surfaceSoft: "#1d222b", ink: "#f4f1e9", muted: "#c4c8d0", rule: "#454b56", axis: "#aab1bc", grey: "#818996", accent: "#8fc4ff", headingFont: "sans" },
};

export const slideThemeStyle = (theme: SlideTheme): CSSProperties => {
  const tokens = SLIDE_THEMES[theme];
  return {
    "--outputs-slide-paper": tokens.paper,
    "--outputs-slide-surface": tokens.surface,
    "--outputs-slide-surface-soft": tokens.surfaceSoft,
    "--outputs-slide-ink": tokens.ink,
    "--outputs-slide-muted": tokens.muted,
    "--outputs-slide-rule": tokens.rule,
    "--outputs-slide-axis": tokens.axis,
    "--outputs-slide-grey": tokens.grey,
    "--outputs-accent-ink": theme === "keynote" ? "#171a20" : "#ffffff",
  } as CSSProperties;
};
