"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  ANALYSIS_HISTORY_CHANGED,
  clearAnalysisHistory,
  loadAnalysisHistory,
  type AnalysisHistoryEntry,
} from "@/lib/analysisHistory";

function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) {
    return "";
  }
  const diffSec = Math.round((Date.now() - t) / 1000);
  if (diffSec < 45) {
    return "Just now";
  }
  if (diffSec < 3600) {
    const m = Math.floor(diffSec / 60);
    return `${m} min ago`;
  }
  if (diffSec < 86400) {
    const h = Math.floor(diffSec / 3600);
    return `${h} h ago`;
  }
  const d = Math.floor(diffSec / 86400);
  return d === 1 ? "Yesterday" : `${d} days ago`;
}

function flowBadge(flow: AnalysisHistoryEntry["flow"]): string {
  return flow === "market" ? "Market" : "Competitive";
}

function truncate(s: string, max: number): string {
  const t = s.trim();
  if (t.length <= max) {
    return t;
  }
  return `${t.slice(0, max - 1)}…`;
}

export function RecentAnalyses() {
  const [items, setItems] = useState<AnalysisHistoryEntry[]>([]);

  const refresh = useCallback(() => {
    setItems(loadAnalysisHistory());
  }, []);

  useEffect(() => {
    refresh();
    const onChange = () => refresh();
    window.addEventListener(ANALYSIS_HISTORY_CHANGED, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(ANALYSIS_HISTORY_CHANGED, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, [refresh]);

  const onClear = () => {
    clearAnalysisHistory();
    setItems([]);
  };

  if (items.length === 0) {
    return (
      <aside className="rounded-xl border border-dashed border-orange-200 bg-orange-50/40 px-4 py-3 text-sm text-zinc-600" aria-label="Recent analyses">
        <p className="font-medium text-zinc-800">Recent analyses</p>
        <p className="mt-1 text-xs text-zinc-500">Runs you start on this device appear here so you can reopen them later.</p>
      </aside>
    );
  }

  const latestLabel = items[0]?.label?.trim();

  return (
    <aside className="rounded-xl border border-zinc-200 bg-white px-4 py-3 shadow-sm" aria-label="Recent analyses">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-zinc-900">Recent analyses</p>
          {latestLabel ? (
            <p className="mt-0.5 text-xs text-zinc-500">
              Latest: <span className="font-medium text-zinc-700">{truncate(latestLabel, 72)}</span>
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClear}
          className="text-xs font-medium text-zinc-500 underline-offset-2 hover:text-orange-700 hover:underline"
        >
          Clear history
        </button>
      </div>
      <ul className="mt-3 max-h-64 space-y-2 overflow-y-auto text-sm">
        {items.map((row) => (
          <li key={row.jobId}>
            <Link
              href={`/jobs/${row.jobId}`}
              className="block rounded-lg border border-transparent px-2 py-1.5 transition hover:border-orange-200 hover:bg-orange-50/40"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                    row.flow === "market" ? "bg-orange-100 text-orange-800" : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {flowBadge(row.flow)}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium text-zinc-800">{row.label}</span>
              </div>
              <div className="mt-0.5 text-xs text-zinc-500">{formatRelativeTime(row.createdAt)}</div>
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}
