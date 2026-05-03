'use client';

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { Disclaimer } from "@/components/Disclaimer";
import { postMarketJob } from "@/lib/api";

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
      router.push(`/jobs/${rsp.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to enqueue market job.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-12">
      <header className="space-y-2">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Market flow</p>
        <h1 className="text-3xl font-semibold">Best Sellers leaderboard</h1>
        <p className="text-zinc-600">
          Paste a category Best Sellers URL. The worker resolves headline ASINs, estimates trailing monthly revenue, and extrapolates
          a blunt whole-market heuristic (clearly labelled as directional only).
        </p>
      </header>

      <Disclaimer />

      <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
        <label htmlFor="bestsellers" className="block text-sm font-medium text-zinc-700">
          Amazon Best Sellers page URL
        </label>
        <input
          id="bestsellers"
          name="bestsellers"
          type="url"
          required
          placeholder="https://www.amazon.com/gp/bestsellers/kitchen/ref=zg_bs_nav_kitchen_1"
          className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 shadow-inner focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
          value={url}
          onChange={(evt) => setUrl(evt.target.value)}
        />
        <p className="text-xs text-zinc-500">
          Production usage should set SCRAPING_PROVIDER=scraperapi with valid ScraperAPI keys; mock mode synthesizes illustrative ASINs for UI QA.
        </p>
        {error ? <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}
        <button
          type="submit"
          disabled={busy || !url}
          className="inline-flex rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-400"
        >
          {busy ? "Queueing..." : "Run market sweep"}
        </button>
      </form>
    </main>
  );
}
