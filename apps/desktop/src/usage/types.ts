export type UsageRange = "today" | "7d" | "30d" | "all";

export interface UsageCounts {
  input: number | null;
  cached: number | null;
  output: number | null;
  reasoning: number | null;
}

export interface UsageTotals extends UsageCounts {
  requests: number;
}

export interface UsageDay extends UsageTotals {
  day: string;
}

export interface UsageSummary {
  totals: UsageTotals;
  byDay: UsageDay[];
  byModel: Array<UsageTotals & { providerId: string; modelId: string }>;
  byJob: Array<UsageTotals & { job: string }>;
}

export interface UsageRequest extends UsageCounts {
  id: string;
  at: string;
  providerId: string;
  modelId: string;
  job: string;
  detail: string | null;
  ref: string | null;
  status: "ok" | "error" | "unreported";
  ms: number | null;
}
