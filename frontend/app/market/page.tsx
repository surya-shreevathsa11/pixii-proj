"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { AppPageHeader } from "@/components/AppPageHeader";
import { RecentAnalyses } from "@/components/RecentAnalyses";
import { Disclaimer } from "@/components/Disclaimer";
import { postMarketJob } from "@/lib/api";
import { pushAnalysisHistoryEntry } from "@/lib/analysisHistory";

export default function MarketWorkspace() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (evt: FormEvent<HTMLFormElement>) => {
    evt.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const rsp = await postMarketJob(url);
      const trimmed = url.trim();
      pushAnalysisHistoryEntry({
        jobId: rsp.job_id,
        flow: "market",
        label: trimmed,
        snapshot: { bestsellers_url: trimmed },
      });
      router.push(`/jobs/${rsp.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to start market analysis.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-8 px-6 py-12">
      <AppPageHeader crumbs={[{ label: "Pixii Market Intel", href: "/" }, { label: "Market sizing" }]} />

      <header className="space-y-3 rounded-2xl border border-orange-200/70 bg-gradient-to-br from-white to-orange-50/45 p-6 shadow-sm">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Market flow</p>
        <h1 className="text-3xl font-semibold tracking-tight text-zinc-900 sm:text-4xl">Best Sellers leaderboard</h1>
        <p className="leading-relaxed text-zinc-600">
          Paste a category Best Sellers URL. The worker resolves headline ASINs, estimates trailing monthly revenue, and extrapolates
          a blunt whole-market heuristic (clearly labelled as directional only).
        </p>
      </header>

      <RecentAnalyses />

      <form
        onSubmit={onSubmit}
        className="space-y-5 rounded-2xl border border-zinc-200/80 bg-white p-6 shadow-sm ring-1 ring-zinc-950/5"
      >
        <label htmlFor="bestsellers" className="block text-sm font-medium text-zinc-700">
          Amazon Best Sellers page URL
        </label>
        <input
          id="bestsellers"
          name="bestsellers"
          type="url"
          required
          placeholder="https://www.amazon.com/gp/bestsellers/kitchen/ref=zg_bs_nav_kitchen_1"
          className="w-full rounded-lg border border-zinc-300 px-3 py-2.5 text-sm text-zinc-900 shadow-inner transition focus:border-orange-600 focus:outline-none focus:ring-2 focus:ring-orange-200"
          value={url}
          onChange={(evt) => setUrl(evt.target.value)}
        />
        <p className="text-xs leading-relaxed text-zinc-500">
          Production usage should set <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">SCRAPING_PROVIDER=scraperapi</code>{" "}
          with a valid ScraperAPI key in <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">backend/.env</code>; mock mode
          synthesizes illustrative ASINs for UI QA.
        </p>
        {error ? <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}
        <button
          type="submit"
          disabled={busy || !url}
          className="inline-flex rounded-lg bg-orange-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-orange-500 disabled:cursor-not-allowed disabled:bg-orange-300"
        >
          {busy ? "Queueing..." : "Run market sweep"}
        </button>
      </form>

      <Disclaimer />
    </main>
  );
}
