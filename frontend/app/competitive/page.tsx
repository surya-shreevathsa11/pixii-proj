'use client';

import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { Disclaimer } from "@/components/Disclaimer";
import { postCompetitiveJob } from "@/lib/api";

export default function CompetitiveWorkspace() {
  const router = useRouter();
  const [mine, setMine] = useState("");
  const [rivalsBlob, setRivalsBlob] = useState("");
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
      const rsp = await postCompetitiveJob({
        product_url: mine.trim(),
        competitor_urls: parsedRivals,
      });
      router.push(`/jobs/${rsp.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to enqueue competitor job.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-12">
      <header className="space-y-2">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-zinc-500">Review intelligence</p>
        <h1 className="text-3xl font-semibold">SKU + rivals console</h1>
        <p className="text-zinc-600">
          Paste your listing URL plus up to nine rivals (one paste per row). Gemini Flash performs map batches on every ~100 snippets and reduces
          the evidence into PDP-ready narratives.
        </p>
      </header>

      <Disclaimer />

      <form onSubmit={onSubmit} className="space-y-6 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
        <div className="space-y-3">
          <label htmlFor="mine" className="block text-sm font-medium text-zinc-700">
            Your listing URL
          </label>
          <input
            id="mine"
            name="mine"
            type="url"
            required
            placeholder="https://www.amazon.com/dp/BXXXXXXXXXX"
            className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 shadow-inner focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
            value={mine}
            onChange={(evt) => setMine(evt.target.value)}
          />
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <label htmlFor="rivals" className="text-sm font-medium text-zinc-700">
              Competitors (one Amazon URL per line, max nine)
            </label>
            <span className="text-xs uppercase tracking-[0.2em] text-zinc-500">{parsedRivals.length}/9 pasted</span>
          </div>
          <textarea
            id="rivals"
            name="rivals"
            rows={6}
            placeholder={[
              "https://www.amazon.com/dp/B0AAAAAAAA",
              "https://www.amazon.com/gp/product/B0BBBBBBBB",
              "https://amazon.com/gp/product/B0CCCCCCCC?ref=mylink",
            ].join("\n")}
            className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 shadow-inner focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
            value={rivalsBlob}
            onChange={(evt) => setRivalsBlob(evt.target.value)}
          />
        </div>

        {error ? <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}

        <button
          type="submit"
          disabled={busy || !ready}
          className="inline-flex rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-400"
        >
          {busy ? "Queueing..." : "Start competitor sweep"}
        </button>
      </form>
    </main>
  );
}
