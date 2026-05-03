export function Disclaimer() {
  return (
    <aside className="rounded-lg border border-amber-400/70 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-950 shadow-sm">
      <p className="font-semibold">Research-only estimates</p>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        <li>Monthly revenue extrapolates BSR snapshots with heuristic math—wide error bars.</li>
        <li>Review insights are synthesized by LLMs—verify against sourcing policy before acting.</li>
        <li>Always respect Amazon + data provider Terms of Use.</li>
      </ul>
    </aside>
  );
}
