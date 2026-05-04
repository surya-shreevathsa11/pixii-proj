"use client";

import { useMemo, useRef, useState } from "react";

import type { PricePoint } from "@/lib/types";

interface PriceHistoryChartProps {
  points: PricePoint[];
  currency: string;
  asin: string;
}

const VIEW_W = 800;
const VIEW_H = 240;
const PAD_L = 56; // room for y-axis labels (price)
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 36; // room for x-axis labels (date)

interface PlotPoint extends PricePoint {
  x: number;
  y: number;
}

function formatPrice(value: number, currency: string): string {
  const code = (currency || "").trim().toUpperCase();
  if (code) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: code,
        maximumFractionDigits: 2,
      }).format(value);
    } catch {
      /* fall through to bare number when the code is non-standard */
    }
  }
  return value.toFixed(2);
}

function formatShortDate(iso: string): string {
  // Parse YYYY-MM-DD without timezone surprises.
  const parts = iso.split("-");
  if (parts.length < 3) return iso;
  const [y, m, d] = parts;
  const year = parseInt(y, 10);
  const month = parseInt(m, 10);
  const day = parseInt(d, 10);
  if (Number.isNaN(year) || Number.isNaN(month) || Number.isNaN(day)) return iso;
  const dt = new Date(Date.UTC(year, month - 1, day));
  return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function PriceHistoryChart({ points, currency, asin }: PriceHistoryChartProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const { plot, minPrice, maxPrice, deltaPct } = useMemo(() => {
    const safe = Array.isArray(points)
      ? points
          .filter(
            (p) =>
              !!p &&
              typeof p.date === "string" &&
              typeof p.price === "number" &&
              Number.isFinite(p.price) &&
              p.price > 0,
          )
          .sort((a, b) => a.date.localeCompare(b.date))
      : [];
    if (safe.length < 2) {
      return { plot: [] as PlotPoint[], minPrice: 0, maxPrice: 0, deltaPct: 0 };
    }
    const prices = safe.map((p) => p.price);
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    const span = hi - lo || 1;
    const padded = span * 0.08; // 8% headroom top + bottom

    const yLo = lo - padded;
    const yHi = hi + padded;
    const yRange = yHi - yLo || 1;
    const xCount = safe.length - 1;
    const innerW = VIEW_W - PAD_L - PAD_R;
    const innerH = VIEW_H - PAD_T - PAD_B;

    const positioned = safe.map((p, i): PlotPoint => {
      const x = PAD_L + (xCount === 0 ? innerW / 2 : (i / xCount) * innerW);
      const y = PAD_T + innerH - ((p.price - yLo) / yRange) * innerH;
      return { ...p, x, y };
    });

    const first = safe[0].price;
    const last = safe[safe.length - 1].price;
    const dPct = first > 0 ? ((last - first) / first) * 100 : 0;

    return { plot: positioned, minPrice: lo, maxPrice: hi, deltaPct: dPct };
  }, [points]);

  if (plot.length < 2) {
    return (
      <div className="flex h-44 items-center justify-center rounded-lg border border-dashed border-zinc-300 bg-zinc-50 text-sm text-zinc-500">
        Not enough price points to chart history yet (need at least two days of data).
      </div>
    );
  }

  const polyline = plot.map((pt) => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" ");
  const innerH = VIEW_H - PAD_T - PAD_B;
  const yMidPrice = (minPrice + maxPrice) / 2;

  const yMaxLabel = formatPrice(maxPrice, currency);
  const yMidLabel = formatPrice(yMidPrice, currency);
  const yMinLabel = formatPrice(minPrice, currency);

  const firstDate = formatShortDate(plot[0].date);
  const lastDate = formatShortDate(plot[plot.length - 1].date);
  const midDate = formatShortDate(plot[Math.floor(plot.length / 2)].date);

  const onMove = (evt: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const cssX = evt.clientX - rect.left;
    const svgX = (cssX / rect.width) * VIEW_W;
    let bestIdx = 0;
    let bestDelta = Number.POSITIVE_INFINITY;
    for (let i = 0; i < plot.length; i += 1) {
      const dx = Math.abs(plot[i].x - svgX);
      if (dx < bestDelta) {
        bestDelta = dx;
        bestIdx = i;
      }
    }
    setHoverIdx(bestIdx);
  };

  const onLeave = () => setHoverIdx(null);

  const active = hoverIdx !== null ? plot[hoverIdx] : null;
  const trendUp = deltaPct >= 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-zinc-600">
        <span>
          <span className="font-semibold text-zinc-700">{plot.length}</span> data points
        </span>
        <span>
          Low: <span className="font-mono">{yMinLabel}</span>
        </span>
        <span>
          High: <span className="font-mono">{yMaxLabel}</span>
        </span>
        <span
          className={
            trendUp
              ? "rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-700"
              : "rounded bg-rose-50 px-1.5 py-0.5 font-medium text-rose-700"
          }
        >
          {trendUp ? "+" : ""}
          {deltaPct.toFixed(1)}% over window
        </span>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        width="100%"
        role="img"
        aria-label={`90-day price history for ASIN ${asin}`}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
        className="block touch-none select-none"
      >
        {[0, 0.5, 1].map((frac) => {
          const y = PAD_T + frac * innerH;
          return (
            <line
              key={frac}
              x1={PAD_L}
              x2={VIEW_W - PAD_R}
              y1={y}
              y2={y}
              stroke="#e4e4e7"
              strokeWidth={1}
              strokeDasharray={frac === 0 || frac === 1 ? "0" : "3 3"}
            />
          );
        })}

        {[
          { label: yMaxLabel, frac: 0 },
          { label: yMidLabel, frac: 0.5 },
          { label: yMinLabel, frac: 1 },
        ].map(({ label, frac }) => (
          <text
            key={label + frac}
            x={PAD_L - 8}
            y={PAD_T + frac * innerH + 4}
            textAnchor="end"
            fontSize={10}
            fill="#71717a"
            fontFamily="ui-sans-serif, system-ui, sans-serif"
          >
            {label}
          </text>
        ))}

        {[
          { label: firstDate, x: PAD_L },
          { label: midDate, x: PAD_L + (VIEW_W - PAD_L - PAD_R) / 2 },
          { label: lastDate, x: VIEW_W - PAD_R },
        ].map(({ label, x }, i) => (
          <text
            key={`${label}-${i}`}
            x={x}
            y={VIEW_H - PAD_B + 18}
            textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
            fontSize={10}
            fill="#71717a"
            fontFamily="ui-sans-serif, system-ui, sans-serif"
          >
            {label}
          </text>
        ))}

        <polyline
          points={polyline}
          fill="none"
          stroke="#2563eb"
          strokeWidth={2.2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {plot.length <= 60 ? (
          plot.map((pt) => (
            <circle key={`${pt.date}-${pt.x}`} cx={pt.x} cy={pt.y} r={2.4} fill="#2563eb" />
          ))
        ) : null}

        {active ? (
          <g pointerEvents="none">
            <line
              x1={active.x}
              x2={active.x}
              y1={PAD_T}
              y2={VIEW_H - PAD_B}
              stroke="#2563eb"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.55}
            />
            <circle cx={active.x} cy={active.y} r={4.5} fill="#fff" stroke="#2563eb" strokeWidth={2} />
            {(() => {
              const labelText = `${formatShortDate(active.date)} \u00b7 ${formatPrice(active.price, currency)}`;
              const charW = 6.4;
              const tipW = Math.max(96, Math.min(220, labelText.length * charW + 16));
              const tipH = 30;
              const tipX = Math.min(
                Math.max(active.x - tipW / 2, PAD_L),
                VIEW_W - PAD_R - tipW,
              );
              const tipY = active.y - tipH - 10 < PAD_T ? active.y + 12 : active.y - tipH - 10;
              return (
                <g>
                  <rect
                    x={tipX}
                    y={tipY}
                    width={tipW}
                    height={tipH}
                    rx={6}
                    fill="#0f172a"
                    opacity={0.92}
                  />
                  <text
                    x={tipX + tipW / 2}
                    y={tipY + 19}
                    textAnchor="middle"
                    fontSize={11}
                    fill="#f8fafc"
                    fontFamily="ui-sans-serif, system-ui, sans-serif"
                  >
                    {labelText}
                  </text>
                </g>
              );
            })()}
          </g>
        ) : null}
      </svg>
    </div>
  );
}

export default PriceHistoryChart;
