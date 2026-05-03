"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { AppPageHeader } from "@/components/AppPageHeader";
import { RecentAnalyses } from "@/components/RecentAnalyses";
import { Disclaimer } from "@/components/Disclaimer";
import { postCompetitiveJob } from "@/lib/api";
import { pushAnalysisHistoryEntry } from "@/lib/analysisHistory";

export default function CompetitiveWorkspace() {
  const router = useRouter();
  const [mine, setMine] = useState("");
  const [rivalsBlob, setRivalsBlob] = useState("");
  const [autoDiscover, setAutoDiscover] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const parsedRivals = useMemo(
    () =>
      rivalsBlob
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean),
    [rivalsBlob],
  );

  const ready = mine.trim().length > 0;

  const onSubmit = async (evt: FormEvent<HTMLFormElement>) => {
    evt.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const productUrl = mine.trim();
      const rsp = await postCompetitiveJob({
        product_url: productUrl,
        competitor_urls: autoDiscover ? [] : parsedRivals,
        auto_discover_competitors: autoDiscover,
      });
      pushAnalysisHistoryEntry({
        jobId: rsp.job_id,
        flow: "competitive",
        label: productUrl,
        snapshot: { product_url: productUrl, auto_discover: autoDiscover },
      });
      router.push(`/jobs/${rsp.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to start competitor analysis.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-12">
      <AppPageHeader crumbs={[{ label: "Pixii Market Intel", href: "/" }, { label: "SKU + rivals study" }]} />

      <header className="space-y-2">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Review intelligence</p>
        <h1 className="text-3xl font-semibold tracking-tight text-zinc-900">SKU + rivals console</h1>
        <p className="leading-relaxed text-zinc-600">
          Enter your product URL or ASIN. By default we pull related ASINs from Amazon&apos;s product page (similar / sponsored
          widgets), then harvest listings and reviews. Turn that off to paste up to nine competitor URLs yourself. The analysis page
          polls the API while the worker runs; Gemini map→reduce needs <code className="rounded bg-zinc-100 px-1 text-xs">GOOGLE_API_KEY</code>{" "}
          when configured.
        </p>
      </header>

      <Disclaimer />

      <RecentAnalyses />

      <form
        onSubmit={onSubmit}
        className="space-y-6 rounded-xl border border-zinc-200/80 bg-white p-6 shadow-sm ring-1 ring-zinc-950/5"
      >
        <div className="space-y-3">
          <label htmlFor="mine" className="block text-sm font-medium text-zinc-700">
            Your listing (Amazon URL or ASIN)
          </label>
          <input
            id="mine"
            name="mine"
            type="text"
            required
            placeholder="https://www.amazon.com/dp/B0XXXXXXXXX or B0XXXXXXXXX"
            className="w-full rounded-lg border border-zinc-300 px-3 py-2.5 text-sm text-zinc-900 shadow-inner transition focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
            value={mine}
            onChange={(evt) => setMine(evt.target.value)}
          />
          <p className="text-xs text-zinc-500">
            Accepts full URLs (with or without https), mobile <code className="rounded bg-zinc-100 px-1">/gp/aw/d/</code>,{" "}
            <code className="rounded bg-zinc-100 px-1">?asin=</code>, bare ASINs, and short links like{" "}
            <code className="rounded bg-zinc-100 px-1">amzn.in</code> / <code className="rounded bg-zinc-100 px-1">amzn.to</code> (the API
            follows redirects to read the final ASIN).
          </p>
        </div>

        <div className="flex items-start gap-3 rounded-lg border border-zinc-200 bg-zinc-50/80 px-4 py-3">
          <input
            id="autoDiscover"
            name="autoDiscover"
            type="checkbox"
            className="mt-1 h-4 w-4 rounded border-zinc-300 text-blue-600 focus:ring-blue-500"
            checked={autoDiscover}
            onChange={(evt) => setAutoDiscover(evt.target.checked)}
          />
          <div>
            <label htmlFor="autoDiscover" className="text-sm font-medium text-zinc-900">
              Find competitors automatically from the product page
            </label>
            <p className="mt-1 text-xs leading-relaxed text-zinc-600">
              Uses <code className="rounded bg-white px-1">data-asin</code> tiles on the PDP (similar items, compare, sponsored). Amazon
              changes markup often—if discovery returns nothing, try <code className="rounded bg-white px-1">SCRAPERAPI_RENDER=true</code>{" "}
              or uncheck this and paste competitor links.
            </p>
          </div>
        </div>

        {!autoDiscover ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="rivals" className="text-sm font-medium text-zinc-700">
                Competitors (one Amazon URL or ASIN per line, max nine)
              </label>
              <span className="shrink-0 text-xs uppercase tracking-[0.2em] text-zinc-500">{parsedRivals.length}/9</span>
            </div>
            <textarea
              id="rivals"
              name="rivals"
              rows={6}
              placeholder={[
                "https://www.amazon.com/dp/B0AAAAAAAA",
                "B0BBBBBBBB",
                "https://amazon.com/gp/product/B0CCCCCCCC?ref=mylink",
              ].join("\n")}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2.5 text-sm text-zinc-900 shadow-inner transition focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
              value={rivalsBlob}
              onChange={(evt) => setRivalsBlob(evt.target.value)}
            />
          </div>
        ) : null}

        <p className="text-xs leading-relaxed text-zinc-500">
          Live PDPs and reviews require <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">SCRAPING_PROVIDER=scraperapi</code> and{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">SCRAPING_API_KEY</code> in <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">backend/.env</code>.
          Competitive analysis keeps up to <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">COMPETITIVE_REVIEWS_PER_ASIN</code> reviews per ASIN (photo-tagged rows are ranked first when available).
          <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px]">GOOGLE_API_KEY</code> enables full Gemini narratives; without it, the analysis page shows fallback summaries.
        </p>

        {error ? <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}

        <button
          type="submit"
          disabled={busy || !ready}
          className="inline-flex rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-400"
        >
          {busy ? "Queueing..." : "Start competitor sweep"}
        </button>
      </form>
    </main>
  );
}
