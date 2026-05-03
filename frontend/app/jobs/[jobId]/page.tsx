'use client';

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Disclaimer } from "@/components/Disclaimer";
import { fetchJob } from "@/lib/api";
import type { JobDetailResponse } from "@/lib/types";

const currencyFmt = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

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

export default function JobInsightPage() {
  const routeParams = useParams<{ jobId?: string | string[] }>();
  const jobId = typeof routeParams?.jobId === "string" ? routeParams.jobId : routeParams?.jobId?.[0];
  const [job, setJob] = useState<JobDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) {
      setError("Missing job identifier.");
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

  const totalEstimated = useMemo(() => {
    const sum = job?.listings.reduce((accum, listing) => accum + (listing.estimated_monthly_revenue ?? 0), 0) ?? 0;
    return sum;
  }, [job]);

  if (!jobId) {
    return null;
  }

  const openerAsin = job?.listings[0]?.asin;

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-10 px-6 py-12">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-zinc-200 pb-6">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Insights board</p>
            <span
              className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${chipVariant(job?.status ?? "queued")}`}
            >
              {job?.status ?? "queued"}
            </span>
          </div>
          <h1 className="text-3xl font-semibold">
            Job{" "}
            <span id="job-hash" className="font-mono text-base text-zinc-500">
              {jobId}
            </span>
          </h1>
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
            New market job
          </Link>
          <Link href="/competitive" className="text-blue-600 hover:text-blue-500">
            New SKU study
          </Link>
          <Link href="/" className="text-blue-600 hover:text-blue-500">
            Overview
          </Link>
        </nav>
      </header>

      <Disclaimer />

      {error ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{error}</p> : null}

      {job?.error_message ? (
        <article className="rounded-lg border border-rose-200 bg-white p-6 text-sm text-rose-900 shadow-sm">
          <h3 className="text-base font-semibold">Trace</h3>
          <pre className="mt-4 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{job.error_message}</pre>
        </article>
      ) : null}

      <section className="grid gap-4 rounded-xl border border-zinc-100 bg-white p-6 shadow-sm md:grid-cols-4">
        <Stat label="Live phase" value={job?.phase ?? "Bootstrapping pipelines"} />
        <Stat label="Listings synthesized" value={job ? String(job.listings.length) : "—"} />
        <Stat label="Captured reviews" value={job ? String(job.reviews_count_total) : "—"} />
        <Stat label="Rolling rev / mo (sum estimates)" value={job ? currencyFmt.format(totalEstimated) : "—"} />
      </section>

      {job?.market_totals_note ? (
        <section className="rounded-xl border border-blue-100 bg-blue-50/60 px-6 py-4 text-sm text-blue-900 shadow-sm">
          <h3 className="text-xs font-semibold uppercase tracking-[0.3em] text-blue-500">Whole-market shorthand</h3>
          <p className="mt-2 whitespace-pre-wrap text-base leading-relaxed">{job.market_totals_note}</p>
        </section>
      ) : null}

      <section className="rounded-xl border border-zinc-100 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">Estimated monthly revenue leaderboard</h2>
            <p className="text-sm text-zinc-500">Sorted descending by illustrative revenue heuristic.</p>
          </div>
        </div>
        <div className="mt-6 overflow-x-auto">
          <table className="min-w-full divide-y divide-zinc-200 text-sm">
            <thead>
              <tr className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500">
                <th className="pb-3 pr-4">#</th>
                <th className="pb-3 pr-4">ASIN</th>
                <th className="pb-3 pr-4">Title</th>
                <th className="pb-3 pr-4 text-right">BSR</th>
                <th className="pb-3 pr-4 text-right">Price</th>
                <th className="pb-3 pr-4 text-right">Est. revenue / mo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {(job?.listings ?? []).map((listing, idx) => (
                <tr key={listing.asin} className="align-top hover:bg-zinc-50">
                  <td className="py-3 pr-4 text-xs font-mono text-zinc-400">{idx + 1}</td>
                  <td className="py-3 pr-4 font-semibold">{listing.asin}</td>
                  <td className="py-3 pr-4 text-zinc-700">
                    {listing.canonical_url ? (
                      <a href={listing.canonical_url} className="underline decoration-blue-400 decoration-2 underline-offset-4">
                        {listing.title.slice(0, 120)}
                        {listing.title.length > 120 ? "…" : ""}
                      </a>
                    ) : (
                      listing.title.slice(0, 120)
                    )}
                    <div className="mt-2 text-[11px] uppercase tracking-[0.2em] text-zinc-400">
                      {listing.bsr_category ?? "Category unknown"}
                    </div>
                  </td>
                  <td className="py-3 pr-4 text-right text-zinc-600">{listing.bsr_rank ?? "—"}</td>
                  <td className="py-3 pr-4 text-right text-zinc-600">
                    {listing.price != null ? `${listing.currency} ${listing.price.toFixed(2)}` : "—"}
                  </td>
                  <td className="py-3 pr-4 text-right font-semibold">
                    {listing.estimated_monthly_revenue != null ? currencyFmt.format(listing.estimated_monthly_revenue) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {job && job.flow === "competitive" ? (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">Purchasing criteria dossiers</h2>
          <div className="space-y-3">
            {(job.listings ?? []).map((listing) => {
              const summary = summariesByAsin.get(listing.asin);
              return (
                <details
                  key={listing.asin}
                  className="group rounded-xl border border-zinc-200 bg-white px-6 py-4 shadow-sm"
                  open={listing.asin === openerAsin}
                >
                  <summary className="cursor-pointer select-none font-semibold text-zinc-900">
                    <span>{listing.asin}</span>
                    <span className="ml-3 text-sm font-normal text-zinc-500">{listing.title.slice(0, 90)}…</span>
                  </summary>
                  <div className="space-y-4 pt-6 text-sm text-zinc-700">
                    {summary?.final_summary ? (
                      <article>
                        <h3 className="text-xs uppercase tracking-[0.3em] text-zinc-400">Executive narrative</h3>
                        <p className="mt-2 whitespace-pre-wrap leading-relaxed">{summary.final_summary}</p>
                      </article>
                    ) : (
                      <p>Gemini output still propagating—or review corpus empty.</p>
                    )}
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
