const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export const count = (value: number | null) => value === null ? "—" : compact.format(value).replace("K", "k");
