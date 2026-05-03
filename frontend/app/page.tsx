import Link from "next/link";

import { RecentAnalyses } from "@/components/RecentAnalyses";
import { Disclaimer } from "@/components/Disclaimer";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-10 px-6 py-12">
      <header className="space-y-4 rounded-2xl border border-zinc-200/80 bg-gradient-to-br from-white to-blue-50/40 p-7 shadow-sm sm:p-8">
        <h1 className="text-4xl font-semibold tracking-tight text-zinc-900 sm:text-5xl">Pixii Market Intel</h1>
        <p className="max-w-3xl text-left text-lg leading-relaxed text-zinc-600 sm:text-[1.1rem]">
          Run a focused market intelligence cycle in minutes: benchmark your SKU against top competitors, estimate
          monthly revenue from live storefront signals, and surface buyer-level demand drivers that actually influence conversion.
        </p>
      </header>

      <RecentAnalyses />

      <section className="grid gap-5 md:grid-cols-2">
        <FlowCard
          title="Market size snapshot"
          body="Paste a Best Sellers category URL. We'll resolve headline ASINs via ScraperAPI (or mock), estimate monthly velocity from BSR, and show a leaderboard."
          href="/market"
          accent="Market"
        />
        <FlowCard
          title="Competitive diagnostics"
          body="Bring your SKU plus competitor URLs, harvest structured listing metadata and up to 1,000 reviews per ASIN, then mine purchase criteria automatically."
          href="/competitive"
          accent="Competitive"
        />
      </section>

      <Disclaimer />
    </main>
  );
}

function FlowCard({
  title,
  body,
  href,
  accent,
}: {
  title: string;
  body: string;
  href: string;
  accent: string;
}) {
  return (
    <article className="group flex flex-col justify-between rounded-2xl border border-zinc-200/90 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="space-y-4">
        <span className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{accent}</span>
        <h2 className="text-xl font-semibold tracking-tight text-zinc-900">{title}</h2>
        <p className="text-sm leading-relaxed text-zinc-600">{body}</p>
      </div>
      <Link
        href={href}
        className="mt-6 inline-flex w-max items-center rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-500"
      >
        Launch workspace
      </Link>
    </article>
  );
}
