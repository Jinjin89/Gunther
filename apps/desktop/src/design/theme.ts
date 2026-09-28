export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "gunther:v3-theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

export const readThemePreference = (): ThemePreference => {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "dark" || stored === "system" ? stored : "light";
};

const systemPrefersDark = () => typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;

export const resolveTheme = (preference: ThemePreference): ResolvedTheme => preference === "system"
  ? systemPrefersDark() ? "dark" : "light"
  : preference;

export const applyTheme = (theme: ResolvedTheme) => {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
};

/** Calls `onChange` whenever the operating system switches between light and dark. */
export const watchSystemTheme = (onChange: () => void): (() => void) => {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const query = window.matchMedia(DARK_QUERY);
  // Safari 13 only implements the older listener API.
  if (typeof query.addEventListener === "function") {
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }
  query.addListener(onChange);
  return () => query.removeListener(onChange);
};
