export function Disclaimer() {
  return (
    <aside className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-xs leading-relaxed text-zinc-600">
      <p className="font-medium text-zinc-700">Research-only estimates</p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-zinc-600">
        <li>Monthly revenue extrapolates BSR snapshots with heuristic math, with wide error bars.</li>
        <li>Review insights are synthesized by LLMs, so verify against sourcing policy before acting.</li>
        <li>Always respect Amazon + data provider Terms of Use.</li>
      </ul>
    </aside>
  );
}
