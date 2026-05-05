import type { JobFlow } from "@/lib/types";

export const ANALYSIS_HISTORY_STORAGE_KEY = "pixii_analysis_history_v1";
export const ANALYSIS_HISTORY_MAX = 40;

/** Loose UUID shape check (hex groups with hyphens). */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type AnalysisHistorySnapshot = {
  bestsellers_url?: string;
  product_url?: string;
  auto_discover?: boolean;
};

export type AnalysisHistoryEntry = {
  jobId: string;
  flow: JobFlow;
  label: string;
  createdAt: string;
  snapshot?: AnalysisHistorySnapshot;
};

export const ANALYSIS_HISTORY_CHANGED = "pixii-analysis-history-changed";

function isValidUuid(id: string): boolean {
  return UUID_RE.test(id.trim());
}

function parseStored(raw: string | null): AnalysisHistoryEntry[] {
  if (!raw) {
    return [];
  }
  try {
    const data = JSON.parse(raw) as unknown;
    if (!Array.isArray(data)) {
      return [];
    }
    const out: AnalysisHistoryEntry[] = [];
    for (const item of data) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const rec = item as Record<string, unknown>;
      const jobId = typeof rec.jobId === "string" ? rec.jobId.trim() : "";
      const flow = rec.flow === "market" || rec.flow === "competitive" ? rec.flow : null;
      const label = typeof rec.label === "string" ? rec.label : "";
      const createdAt = typeof rec.createdAt === "string" ? rec.createdAt : "";
      if (!jobId || !flow || !label || !createdAt || !isValidUuid(jobId)) {
        continue;
      }
      const snap = rec.snapshot;
      let snapshot: AnalysisHistorySnapshot | undefined;
      if (snap && typeof snap === "object") {
        const s = snap as Record<string, unknown>;
        snapshot = {};
        if (typeof s.bestsellers_url === "string") {
          snapshot.bestsellers_url = s.bestsellers_url;
        }
        if (typeof s.product_url === "string") {
          snapshot.product_url = s.product_url;
        }
        if (typeof s.auto_discover === "boolean") {
          snapshot.auto_discover = s.auto_discover;
        }
        if (Object.keys(snapshot).length === 0) {
          snapshot = undefined;
        }
      }
      out.push({ jobId, flow, label, createdAt, snapshot });
    }
    return out;
  } catch {
    return [];
  }
}

export function loadAnalysisHistory(): AnalysisHistoryEntry[] {
  if (typeof window === "undefined") {
    return [];
  }
  return parseStored(window.localStorage.getItem(ANALYSIS_HISTORY_STORAGE_KEY));
}

function persist(entries: AnalysisHistoryEntry[]): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(ANALYSIS_HISTORY_STORAGE_KEY, JSON.stringify(entries));
    window.dispatchEvent(new CustomEvent(ANALYSIS_HISTORY_CHANGED));
  } catch {
    /* quota or private mode */
  }
}

export function truncateLabel(text: string, max = 72): string {
  const t = text.trim().replace(/\s+/g, " ");
  if (t.length <= max) {
    return t;
  }
  return `${t.slice(0, max - 1)}…`;
}

/** Append or refresh an entry (moves duplicate jobId to front). Only runs in the browser. */
export function pushAnalysisHistoryEntry(
  partial: Omit<AnalysisHistoryEntry, "createdAt"> & { createdAt?: string },
): void {
  if (typeof window === "undefined") {
    return;
  }
  if (!isValidUuid(partial.jobId)) {
    return;
  }
  const entry: AnalysisHistoryEntry = {
    jobId: partial.jobId.trim(),
    flow: partial.flow,
    label: truncateLabel(partial.label, 120),
    createdAt: partial.createdAt ?? new Date().toISOString(),
    snapshot: partial.snapshot,
  };
  const prev = loadAnalysisHistory();
  const rest = prev.filter((e) => e.jobId !== entry.jobId);
  const next = [entry, ...rest].slice(0, ANALYSIS_HISTORY_MAX);
  persist(next);
}

export function clearAnalysisHistory(): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.removeItem(ANALYSIS_HISTORY_STORAGE_KEY);
    window.dispatchEvent(new CustomEvent(ANALYSIS_HISTORY_CHANGED));
  } catch {
    /* ignore */
  }
}

export function removeAnalysisHistoryEntry(jobId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  const key = jobId.trim();
  if (!isValidUuid(key)) {
    return;
  }
  const prev = loadAnalysisHistory();
  const next = prev.filter((e) => e.jobId !== key);
  persist(next);
}
