"use client";

import { percent } from "@/lib/format";

/**
 * The slab ruler.
 *
 * Achievement is not a smooth curve in this scheme — it steps at 50%, 80%,
 * 100% and 130%, and the step you land on multiplies everything you earned.
 * A progress bar would hide that. This draws the steps as a ruler and marks
 * where you stand, so "how far to the next rate" is readable at a glance.
 */

interface Slab {
  from: number;
  rate: number;
}

export function SlabRuler({
  value,
  slabs,
  currentRate,
}: {
  value: number;
  slabs: Slab[];
  currentRate: number;
}) {
  const max = Math.max(1.6, value * 1.05);
  const pos = Math.min(value, max) / max;
  const next = slabs.find((s) => s.from > value);

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <div>
          <div className="label">Achievement</div>
          <div className="text-3xl font-semibold tabular">{percent(value)}</div>
        </div>
        <div className="text-right">
          <div className="label">Your rate</div>
          <div className="text-3xl font-semibold tabular text-teal">
            {percent(currentRate, 2)}
          </div>
        </div>
      </div>

      <div className="relative mt-6 h-11">
        {/* the bands */}
        <div className="absolute inset-x-0 top-3 flex h-3 overflow-hidden rounded-card">
          {slabs.map((slab, i) => {
            const end = slabs[i + 1]?.from ?? max;
            const width = ((Math.min(end, max) - slab.from) / max) * 100;
            if (width <= 0) return null;
            const reached = value >= slab.from;
            return (
              <div
                key={slab.from}
                style={{ width: `${width}%` }}
                className={reached ? "bg-teal" : "bg-rule"}
                title={`${percent(slab.from, 0)} and above pays ${percent(slab.rate, 2)}`}
              />
            );
          })}
        </div>

        {/* threshold ticks */}
        {slabs.slice(1).map((slab) => (
          <div
            key={slab.from}
            className="absolute top-0"
            style={{ left: `${(slab.from / max) * 100}%` }}
          >
            <div className="h-3 w-px bg-ink-faint" />
            <div className="mt-[18px] -translate-x-1/2 whitespace-nowrap text-micro text-ink-faint">
              {percent(slab.from, 0)}
            </div>
          </div>
        ))}

        {/* where you are */}
        <div
          className="absolute top-1"
          style={{ left: `${pos * 100}%` }}
          aria-hidden
        >
          <div className="h-7 w-[3px] -translate-x-1/2 rounded bg-ink" />
        </div>
      </div>

      <p className="mt-7 text-sm text-ink-muted">
        {next ? (
          <>
            Reach {percent(next.from, 0)} and the rate on your whole qualified
            revenue rises to {percent(next.rate, 2)}.
          </>
        ) : (
          <>You are in the top slab. This is the highest rate the policy pays.</>
        )}
      </p>
    </div>
  );
}
