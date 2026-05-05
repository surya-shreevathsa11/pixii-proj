"use client";

import { useEffect, useState } from "react";

import { fetchBootstrap } from "@/lib/api";

const STORAGE_KEY = "pixii_bootstrap_strip_dismissed_v1";

function isLiveScraping(provider: string): boolean {
  const p = provider.toLowerCase().trim();
  return p === "scraperapi" || p === "scraper_api";
}

export function BootstrapStrip() {
  const [dismissed, setDismissed] = useState(false);
  const [mock, setMock] = useState<boolean | null>(null);
  const [claude, setClaude] = useState<boolean | null>(null);
  const [youtube, setYoutube] = useState<boolean | null>(null);

  useEffect(() => {
    try {
      if (typeof window !== "undefined" && window.sessionStorage.getItem(STORAGE_KEY) === "1") {
        setDismissed(true);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchBootstrap();
        if (!cancelled) {
          setMock(!isLiveScraping(data.scraping_provider));
          setClaude(data.claude_configured);
          setYoutube(data.youtube_configured ?? false);
        }
      } catch {
        if (!cancelled) {
          setMock(null);
          setClaude(null);
          setYoutube(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onDismiss = () => {
    setDismissed(true);
    try {
      window.sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
  };

  if (dismissed || mock === null || claude === null || youtube === null) {
    return null;
  }

  if (!mock && claude && youtube) {
    return null;
  }

  return (
    <div
      className="border-b border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-950"
      role="status"
    >
      <div className="mx-auto flex max-w-4xl flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          {mock ? (
            <p>
              <span className="font-semibold">Demo ingest:</span> the API is not using ScraperAPI, so listings and reviews are
              synthetic. For real Amazon PDP data, set{" "}
              <code className="rounded bg-orange-100/80 px-1 py-0.5 text-xs">SCRAPING_PROVIDER=scraperapi</code> and{" "}
              <code className="rounded bg-orange-100/80 px-1 py-0.5 text-xs">SCRAPING_API_KEY</code> in{" "}
              <code className="rounded bg-orange-100/80 px-1 py-0.5 text-xs">backend/.env</code>, then restart the server.
            </p>
          ) : null}
          {!mock && claude === false ? (
            <p>
              <span className="font-semibold">Claude off:</span> add <code className="rounded bg-orange-100/80 px-1 py-0.5 text-xs">ANTHROPIC_API_KEY</code>{" "}
              for live map→reduce narratives on competitive jobs; otherwise summaries use built-in stubs.
            </p>
          ) : null}
          {!mock && claude && youtube === false ? (
            <p>
              <span className="font-semibold">YouTube appendix off:</span> add{" "}
              <code className="rounded bg-orange-100/80 px-1 py-0.5 text-xs">YOUTUBE_DATA_API_KEY</code> for competitive YouTube
              demand scores and review-video links on new runs.
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded-md border border-orange-300 bg-white px-2 py-1 text-xs font-medium text-orange-900 shadow-sm hover:bg-orange-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
