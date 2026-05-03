"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AppPageHeader } from "@/components/AppPageHeader";
import { RecentAnalyses } from "@/components/RecentAnalyses";
import { Disclaimer } from "@/components/Disclaimer";
import { fetchJob } from "@/lib/api";
import type { JobDetailResponse } from "@/lib/types";

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  currencyDisplay: "narrowSymbol",
  maximumFractionDigits: 0,
});

const inrPriceFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  currencyDisplay: "narrowSymbol",
  maximumFractionDigits: 2,
});

const numberFmt = new Intl.NumberFormat("en-IN");

function truncateHeadline(s: string, max: number): string {
  const t = s.trim();
  if (t.length <= max) {
    return t;
  }
  return `${t.slice(0, max - 1)}…`;
}

function analysisPageHeadline(job: JobDetailResponse | null): { title: string; subtitle?: string } {
  if (!job) {
    return { title: "Analysis" };
  }
  const primaryAsin = job.asins[0];
  const primaryListing = job.listings.find((l) => l.asin === primaryAsin) ?? job.listings[0];
  const listingTitle = (primaryListing?.title || "").trim();

  if (job.flow === "competitive") {
    if (listingTitle) {
      return { title: truncateHeadline(listingTitle, 140), subtitle: "Competitive diagnostics" };
    }
    const url = job.product_url?.trim();
    if (url) {
      return { title: truncateHeadline(url, 100), subtitle: "Competitive diagnostics" };
    }
    return { title: "Competitive diagnostics" };
  }

  if (listingTitle) {
    return { title: truncateHeadline(listingTitle, 140), subtitle: "Market sizing" };
  }
  const bu = job.bestsellers_url?.trim();
  if (bu) {
    return { title: "Market sizing", subtitle: truncateHeadline(bu, 120) };
  }
  return { title: "Market sizing" };
}

const STAR_TITLE_PREFIX = /^\s*[\d.]+\s*out\s+of\s*5\s*stars\s*/i;

function cleanReviewTitle(title: string | null | undefined): string | undefined {
  if (!title) {
    return undefined;
  }
  const t = title.replace(STAR_TITLE_PREFIX, "").trim();
  return t || undefined;
}

const PHOTO_REVIEW_PREFIX = "[Customer photos in review]";

function cleanReviewBody(body: string): string {
  let b = body.trim();
  if (b.startsWith(PHOTO_REVIEW_PREFIX)) {
    b = b.slice(PHOTO_REVIEW_PREFIX.length).trim();
  }
  return b;
}

function formatListingCategory(listing: JobDetailResponse["listings"][number]) {
  const browse = listing.product_category?.trim();
  if (browse) {
    return browse;
  }
  return listing.bsr_category?.trim() || "Category unknown";
}

function formatStarLine(listing: JobDetailResponse["listings"][number]) {
  const rating = listing.avg_rating;
  const rc = listing.review_count;
  if (rating == null && rc == null) {
    return null;
  }
  const parts: string[] = [];
  if (rating != null) {
    parts.push(`★ ${rating.toFixed(1)}`);
  }
  if (rc != null) {
    parts.push(`${numberFmt.format(rc)} reviews`);
  }
  return parts.join(" · ");
}

function basisChipClass(basis: string) {
  switch (basis) {
    case "bought_past_month":
      return "bg-emerald-100 text-emerald-900";
    case "bsr_heuristic":
      return "bg-amber-100 text-amber-900";
    default:
      return "bg-zinc-100 text-zinc-700";
  }
}

function basisChipLabel(basis: string) {
  switch (basis) {
    case "bought_past_month":
      return "bought in past month";
    case "bsr_heuristic":
      return "BSR heuristic";
    default:
      return "no signal";
  }
}

function chipVariant(status: JobDetailResponse["status"]) {
  switch (status) {
    case "completed":
      return "bg-emerald-100 text-emerald-900";
    case "failed":
      return "bg-rose-100 text-rose-900";
    case "running":
      return "bg-amber-100 text-amber-900";
    default:
      return "bg-zinc-100 text-zinc-800";
  }
}

function JobLoadingSkeleton() {
  return (
    <div className="animate-pulse space-y-6" aria-busy="true" aria-label="Loading analysis">
      <div className="h-10 w-48 rounded bg-zinc-200" />
      <div className="h-24 rounded-xl bg-zinc-100" />
      <div className="grid gap-4 md:grid-cols-4">
        {[1, 2, 3, 4].map((k) => (
          <div key={k} className="h-20 rounded-lg bg-zinc-100" />
        ))}
      </div>
      <div className="h-64 rounded-xl bg-zinc-100" />
    </div>
  );
}

export default function JobInsightPage() {
  const routeParams = useParams<{ jobId?: string | string[] }>();
  const jobId = typeof routeParams?.jobId === "string" ? routeParams.jobId : routeParams?.jobId?.[0];
  const [job, setJob] = useState<JobDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) {
      setError("Missing analysis identifier.");
      return undefined;
    }

    let disposed = false;
    let handle: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      if (disposed) {
        return;
      }
      try {
        const rsp = await fetchJob(jobId);
        if (!disposed) {
          setJob(rsp);
          setError(null);
          const terminal = rsp.status === "completed" || rsp.status === "failed";
          if (terminal && handle !== undefined) {
            clearInterval(handle);
            handle = undefined;
          }
        }
      } catch (err) {
        if (!disposed) {
          setError(err instanceof Error ? err.message : "Polling error.");
        }
      }
    };

    tick();
    handle = setInterval(() => void tick(), 2500);

    return () => {
      disposed = true;
      if (handle !== undefined) {
        clearInterval(handle);
      }
    };
  }, [jobId]);

  const summariesByAsin = useMemo(() => {
    const lookup = new Map<string, JobDetailResponse["summaries"][number]>();
    job?.summaries.forEach((row) => lookup.set(row.asin, row));
    return lookup;
  }, [job]);

  const reviewsByAsin = useMemo(() => {
    const map = new Map<string, NonNullable<JobDetailResponse["reviews"]>>();
    (job?.reviews ?? []).forEach((row) => {
      const bucket = map.get(row.asin) ?? [];
      bucket.push(row);
      map.set(row.asin, bucket);
    });
    map.forEach((rows, key) => {
      map.set(key, [...rows].reverse());
    });
    return map;
  }, [job]);

  const totalEstimated = useMemo(() => {
    const sum = job?.listings.reduce((accum, listing) => accum + (listing.estimated_monthly_revenue ?? 0), 0) ?? 0;
    return sum;
  }, [job]);

  const headline = useMemo(() => analysisPageHeadline(job), [job]);

  if (!jobId) {
    return null;
  }

  const openerAsin = job?.listings[0]?.asin;
  const ingestDemo = job?.ingest_demo ?? false;
  const geminiConfigured = job?.gemini_configured ?? false;
  const isActive = job && (job.status === "queued" || job.status === "running");
  const emptyCompetitiveReviews =
    job?.flow === "competitive" && job.status === "completed" && job.reviews_count_total === 0;

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-10 px-6 py-12">
      <AppPageHeader
        crumbs={[
          { label: "Pixii Market Intel", href: "/" },
          { label: "Insights", href: "/market" },
          { label: truncateHeadline(headline.title, 48) },
        ]}
      />

      <RecentAnalyses />

      <header className="flex flex-wrap items-start justify-between gap-4 pb-2">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Pixii Market Intel</p>
            <span
              className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${chipVariant(job?.status ?? "queued")}`}
            >
              {job?.status ?? "queued"}
            </span>
          </div>
          <h1 className="text-3xl font-semibold leading-snug text-zinc-900">{headline.title}</h1>
          {headline.subtitle ? <p className="text-sm text-zinc-600">{headline.subtitle}</p> : null}
          <div className="space-y-1 text-sm text-zinc-600">
            <div>
              Flow:{" "}
              <span className="font-semibold text-zinc-900">
                {job ? (job.flow === "market" ? "Market sizing" : "Competitive diagnostics") : "…"}
              </span>
            </div>
            <div>
              Telemetry:{" "}
              <span className="font-semibold text-zinc-900">
                {job ? (job.phase || "Hydrating ingest") : "Synchronizing dashboards"}
              </span>
            </div>
            <div className="text-xs text-zinc-500">Tracked ASINs: {job?.asins.length ?? 0}</div>
          </div>
        </div>
        <nav className="flex flex-wrap gap-3 text-sm">
          <Link href="/market" className="text-blue-600 hover:text-blue-500">
            New market analysis
          </Link>
          <Link href="/competitive" className="text-blue-600 hover:text-blue-500">
            New SKU study
          </Link>
          <Link href="/" className="text-blue-600 hover:text-blue-500">
            Overview
          </Link>
        </nav>
      </header>

      {ingestDemo && job ? (
        <div
          className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
          role="status"
        >
          <p className="font-semibold">Synthetic data detected for this analysis</p>
          <p className="mt-1 text-amber-900/90">
            Listings match mock or pre-ingest settings (titles like “Demo product” are expected). For live Amazon PDPs,
            configure ScraperAPI in <code className="rounded bg-amber-100/90 px-1 text-xs">backend/.env</code> and run new
            analyses after restarting the API.
          </p>
        </div>
      ) : null}

      {job?.flow === "competitive" && !geminiConfigured ? (
        <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-800" role="status">
          <p className="font-semibold">Gemini not configured on the server</p>
          <p className="mt-1 text-zinc-700">
            Dossiers below use offline stubs until you set <code className="rounded bg-white px-1 text-xs">GOOGLE_API_KEY</code>{" "}
            and restart the API.
          </p>
        </div>
      ) : null}

      <Disclaimer />

      {error ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{error}</p> : null}

      {job === null && !error ? <JobLoadingSkeleton /> : null}

      {job?.error_message ? (
        <article className="rounded-lg border border-rose-200 bg-white p-6 text-sm text-rose-900 shadow-sm">
          <h3 className="text-base font-semibold">Trace</h3>
          <pre className="mt-4 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{job.error_message}</pre>
        </article>
      ) : null}

      {isActive ? (
        <div className="space-y-2 rounded-lg border border-blue-100 bg-blue-50/50 px-4 py-3">
          <div className="flex items-center justify-between gap-3 text-xs font-medium uppercase tracking-wide text-blue-800">
            <span>Pipeline</span>
            <span className="font-mono normal-case text-blue-900">{job?.phase ?? "…"}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-blue-100">
            <div
              className="h-full w-1/3 animate-pulse rounded-full bg-blue-500"
              style={{ animationDuration: "1.8s" }}
            />
          </div>
          <p className="text-xs text-blue-800/90">This page refreshes every few seconds until processing finishes.</p>
        </div>
      ) : null}

      {job ? (
        <section className="grid gap-4 rounded-xl border border-zinc-100 bg-white p-6 shadow-sm md:grid-cols-4">
          <Stat label="Live phase" value={job.phase || "—"} />
          <Stat label="Listings synthesized" value={String(job.listings.length)} />
          <Stat label="Captured reviews" value={String(job.reviews_count_total)} />
          <Stat label="Rolling rev / mo (sum estimates, INR)" value={inrFmt.format(totalEstimated)} />
        </section>
      ) : null}

      {job?.market_totals_note ? (
        <section className="rounded-xl border border-blue-100 bg-blue-50/60 px-6 py-4 text-sm text-blue-900 shadow-sm">
          <h3 className="text-xs font-semibold uppercase tracking-[0.3em] text-blue-500">Whole-market shorthand</h3>
          <p className="mt-2 whitespace-pre-wrap text-base leading-relaxed">{job.market_totals_note}</p>
        </section>
      ) : null}

      {job && job.flow === "competitive" ? (
        <nav className="flex flex-wrap items-center gap-3 text-sm text-zinc-600" aria-label="On-page sections">
          <span className="font-medium text-zinc-900">Jump to:</span>
          <a href="#leaderboard" className="rounded-md text-blue-600 underline-offset-2 hover:underline">
            Revenue leaderboard
          </a>
          <span className="text-zinc-300">·</span>
          <a href="#dossiers" className="rounded-md text-blue-600 underline-offset-2 hover:underline">
            Review dossiers
          </a>
        </nav>
      ) : null}

      {job ? (
        <section id="leaderboard" className="scroll-mt-24 rounded-xl border border-zinc-100 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold">Estimated monthly revenue leaderboard</h2>
              <p className="text-sm text-zinc-500">
                Previous-month sales x unit price (INR). Falls back to a BSR-rank heuristic when Amazon hides the
                "bought in past month" badge.
              </p>
              {job.flow === "competitive" ? (
                <p className="mt-2 text-xs text-zinc-500">
                  Competitive analyses also populate{" "}
                  <a href="#dossiers" className="text-blue-600 hover:underline">
                    review dossiers
                  </a>{" "}
                  below after harvest and summarization.
                </p>
              ) : null}
            </div>
          </div>
          <div className="mt-6 overflow-x-auto rounded-lg border border-zinc-100">
            <table className="min-w-full divide-y divide-zinc-200 text-sm">
              <thead className="bg-zinc-50/80">
                <tr className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  <th className="px-4 py-3 pr-4">#</th>
                  <th className="max-w-lg px-0 py-3 pr-4">ASIN / product</th>
                  <th className="px-0 py-3 pr-4 text-right">BSR</th>
                  <th className="px-0 py-3 pr-4 text-right">Prev-month units</th>
                  <th className="px-0 py-3 pr-4 text-right">Price</th>
                  <th className="px-4 py-3 text-right">Est. revenue / mo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {job.listings.map((listing, idx) => {
                  const priceInr = listing.unit_price_inr ?? (listing.currency === "INR" ? listing.price : null);
                  const showOriginalPrice =
                    listing.price != null && listing.currency && listing.currency.toUpperCase() !== "INR";
                  return (
                    <tr key={listing.asin} className="align-top transition hover:bg-zinc-50/80">
                      <td className="px-4 py-3 pr-4 text-xs font-mono text-zinc-400">{idx + 1}</td>
                      <td className="max-w-lg py-3 pr-4 text-zinc-700">
                        <div className="font-mono text-xs font-semibold tracking-wide text-zinc-900">{listing.asin}</div>
                        <div className="mt-1.5">
                          {listing.canonical_url ? (
                            <a
                              href={listing.canonical_url}
                              className="text-sm font-medium text-blue-700 underline decoration-blue-400 decoration-2 underline-offset-4 hover:text-blue-600"
                            >
                              {listing.title.slice(0, 140)}
                              {listing.title.length > 140 ? "…" : ""}
                            </a>
                          ) : (
                            <span className="text-sm font-medium text-zinc-900">
                              {listing.title.slice(0, 140)}
                              {listing.title.length > 140 ? "…" : ""}
                            </span>
                          )}
                        </div>
                        {formatStarLine(listing) ? (
                          <div className="mt-1.5 text-xs font-medium text-zinc-600">{formatStarLine(listing)}</div>
                        ) : null}
                        <div className="mt-2 text-[11px] uppercase tracking-[0.2em] text-zinc-400">
                          {formatListingCategory(listing)}
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-right text-zinc-600">{listing.bsr_rank ?? "—"}</td>
                      <td className="py-3 pr-4 text-right text-zinc-600">
                        {listing.previous_month_units != null
                          ? numberFmt.format(listing.previous_month_units)
                          : "—"}
                      </td>
                      <td className="py-3 pr-4 text-right text-zinc-600">
                        {priceInr != null ? (
                          <div className="space-y-0.5">
                            <div>{inrPriceFmt.format(priceInr)}</div>
                            {showOriginalPrice && listing.price != null ? (
                              <div className="text-[10px] uppercase tracking-wide text-zinc-400">
                                {listing.currency} {listing.price.toFixed(2)}
                              </div>
                            ) : null}
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="font-semibold text-zinc-900">
                          {listing.estimated_monthly_revenue != null
                            ? inrFmt.format(listing.estimated_monthly_revenue)
                            : "—"}
                        </div>
                        <span
                          className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${basisChipClass(
                            listing.revenue_basis,
                          )}`}
                        >
                          {basisChipLabel(listing.revenue_basis)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {emptyCompetitiveReviews ? (
        <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-900" role="status">
          <p className="font-semibold">No reviews were stored for this competitive analysis</p>
          <p className="mt-1 text-rose-800/90">
            Competitive analyses normally keep up to ten recent reviews per ASIN (photo reviews ranked first when the
            scraper marks them). Empty rows usually mean the reviews page did not parse—try{" "}
            <code className="rounded bg-white px-1 text-xs">SCRAPERAPI_RENDER=true</code>, confirm{" "}
            <code className="rounded bg-white px-1 text-xs">AMAZON_DOMAIN</code> matches the storefront, or check ScraperAPI
            quotas and HTML samples.
          </p>
        </div>
      ) : null}

      {job && job.flow === "competitive" ? (
        <section id="dossiers" className="scroll-mt-24 space-y-4">
          <h2 className="text-xl font-semibold">Purchasing criteria dossiers</h2>
          <div className="space-y-3">
            {(job.listings ?? []).map((listing) => {
              const summary = summariesByAsin.get(listing.asin);
              const displayTitle = (summary?.product_title || listing.title || "").trim() || "—";
              const titleSnippet = displayTitle.length > 100 ? `${displayTitle.slice(0, 100)}…` : displayTitle;
              const asinReviews = reviewsByAsin.get(listing.asin) ?? [];
              return (
                <details
                  key={listing.asin}
                  className="group rounded-xl border border-zinc-200 bg-white px-6 py-4 shadow-sm"
                  open={listing.asin === openerAsin}
                >
                  <summary className="cursor-pointer select-none text-zinc-900">
                    <span className="font-mono text-sm font-semibold tracking-wide">{listing.asin}</span>
                    <span className="mt-1 block text-sm font-normal text-zinc-600">{titleSnippet}</span>
                    {formatStarLine(listing) ? (
                      <span className="mt-1 block text-xs font-medium text-zinc-500">{formatStarLine(listing)}</span>
                    ) : null}
                    <span className="mt-1 block text-[11px] uppercase tracking-[0.2em] text-zinc-400">
                      {formatListingCategory(listing)}
                    </span>
                  </summary>
                  <div className="space-y-4 pt-6 text-sm text-zinc-700">
                    {asinReviews.length ? (
                      <details className="rounded-lg border border-zinc-100 bg-zinc-50/60 px-4 py-3" open>
                        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
                          Latest reviews ({asinReviews.length})
                        </summary>
                        <ul className="mt-4 space-y-4">
                          {asinReviews.map((rv, idx) => {
                            const bodyClean = cleanReviewBody(rv.body);
                            const preview =
                              bodyClean.length > 360 ? `${bodyClean.slice(0, 360).trimEnd()}…` : bodyClean;
                            const rvTitle = cleanReviewTitle(rv.title);
                            const metaLine = [
                              rv.rating != null ? `★ ${rv.rating}` : null,
                              rv.review_date,
                              rv.verified ? "Verified" : null,
                            ]
                              .filter(Boolean)
                              .join(" · ");
                            return (
                              <li key={`${listing.asin}-rv-${idx}`} className="text-sm">
                                <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                                  <span>{metaLine || "Review"}</span>
                                  {rv.has_customer_images ? (
                                    <span className="rounded bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-900">
                                      Photos
                                    </span>
                                  ) : null}
                                </div>
                                {rvTitle ? <p className="mt-1 font-medium text-zinc-800">{rvTitle}</p> : null}
                                <p className="mt-1 leading-relaxed text-zinc-600">{preview}</p>
                              </li>
                            );
                          })}
                        </ul>
                      </details>
                    ) : null}
                    {summary?.final_summary ? (
                      <article>
                        <h3 className="text-xs uppercase tracking-[0.3em] text-zinc-400">Executive narrative</h3>
                        <p className="mt-2 whitespace-pre-wrap leading-relaxed">{summary.final_summary}</p>
                      </article>
                    ) : (
                      <p>Gemini output still propagating—or review corpus empty.</p>
                    )}
                    {summary?.why_buyers_like ? (
                      <article>
                        <h3 className="text-xs uppercase tracking-[0.3em] text-zinc-400">Why buyers like it</h3>
                        <p className="mt-2 whitespace-pre-wrap leading-relaxed">{summary.why_buyers_like}</p>
                      </article>
                    ) : null}
                    {summary?.why_buyers_caution ? (
                      <article>
                        <h3 className="text-xs uppercase tracking-[0.3em] text-zinc-400">Why buyers caution</h3>
                        <p className="mt-2 whitespace-pre-wrap leading-relaxed">{summary.why_buyers_caution}</p>
                      </article>
                    ) : null}
                    {summary?.key_purchase_criteria?.length ? (
                      <div>
                        <h3 className="text-xs uppercase tracking-[0.3em] text-zinc-400">Key PDP purchase criteria</h3>
                        <ul className="mt-3 list-disc space-y-2 pl-5">
                          {summary.key_purchase_criteria.map((criterion) => (
                            <li key={criterion}>{criterion}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                </details>
              );
            })}
          </div>
        </section>
      ) : null}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.3em] text-zinc-500">{label}</p>
      <p className="mt-3 text-xl font-semibold text-zinc-900">{value}</p>
    </div>
  );
}
