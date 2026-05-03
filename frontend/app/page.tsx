import Link from "next/link";

import { Disclaimer } from "@/components/Disclaimer";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-10 px-6 py-16">
      <header className="space-y-3">
        <h1 className="text-4xl font-semibold tracking-tight text-zinc-900">Pixii Market Intel</h1>
        <p className="text-lg text-zinc-600">
          Queue a Best Sellers scrape or ingest your listing versus nine rivals. The dashboard ranks estimated revenue from
          storefront telemetry and summarizes review themes via map→reduce Gemini calls.
        </p>
      </header>

      <Disclaimer />

      <section className="grid gap-4 md:grid-cols-2">
        <FlowCard
          title="Market size snapshot"
          body="Paste a Best Sellers category URL. We'll resolve headline ASINs via ScraperAPI (or mock), estimate monthly velocity from BSR, and show a leaderboard."
          href="/market"
          accent="Market"
        />
        <FlowCard
          title="Competitive diagnostics"
          body="Bring your SKU plus competitor URLs—harvest structured listing metadata and up to 1,000 reviews per ASIN, then mine purchase criteria automatically."
          href="/competitive"
          accent="Competitive"
        />
      </section>
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
    <article className="flex flex-col justify-between rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
      <div className="space-y-4">
        <span className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{accent}</span>
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="text-sm leading-relaxed text-zinc-600">{body}</p>
      </div>
      <Link
        href={href}
        className="mt-6 inline-flex w-max items-center text-sm font-semibold text-blue-600 hover:text-blue-500"
      >
        Launch workspace →
      </Link>
    </article>
  );
}
