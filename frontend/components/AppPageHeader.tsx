"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

export type AppPageCrumb = { label: string; href?: string };

export function AppPageHeader({
  crumbs,
  backLabel = "Back",
}: {
  crumbs: AppPageCrumb[];
  backLabel?: string;
}) {
  const router = useRouter();

  return (
    <div className="mb-8 flex flex-col gap-4 rounded-xl border border-zinc-200/80 bg-white/80 p-4 shadow-sm backdrop-blur">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button
          type="button"
          onClick={() => router.back()}
          className="inline-flex items-center rounded-md border border-zinc-200 px-2.5 py-1 text-zinc-700 underline-offset-2 transition hover:bg-zinc-100 hover:text-zinc-900"
        >
          {backLabel}
        </button>
        <span className="select-none text-zinc-300">·</span>
        <Link href="/" className="rounded-md px-2 py-1 text-blue-700 transition hover:bg-blue-50 hover:text-blue-600">
          Pixii Market Intel
        </Link>
      </div>
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
        {crumbs.map((c, i) => (
          <span key={`${c.label}-${i}`} className="inline-flex items-center gap-2">
            {i > 0 ? <span className="text-zinc-300" aria-hidden>/</span> : null}
            {c.href ? (
              <Link href={c.href} className="font-medium text-zinc-600 transition hover:text-blue-600">
                {c.label}
              </Link>
            ) : (
              <span className="font-semibold text-zinc-900">{c.label}</span>
            )}
          </span>
        ))}
      </nav>
    </div>
  );
}
