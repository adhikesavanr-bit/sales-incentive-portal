"use client";

import { monthLabel, recentPeriods } from "@/lib/format";
import { rangeOptions, selectionLabel } from "@/lib/period";

/** Months always; quarters and the financial year only where the page can show them. */
export function PeriodPicker({
  value,
  onChange,
  withRanges = false,
}: {
  value: string;
  onChange: (period: string) => void;
  withRanges?: boolean;
}) {
  const months = recentPeriods();
  // A month outside the recent list (picked earlier) must stay selectable.
  const extra = /^\d{4}-\d{2}$/.test(value) && !months.includes(value);
  return (
    <label className="inline-flex items-center gap-2 text-sm">
      <span className="sr-only">Period</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-card border border-rule bg-surface px-3 py-2 text-sm"
      >
        <optgroup label="Month">
          {extra && <option value={value}>{monthLabel(value)}</option>}
          {months.map((p) => (
            <option key={p} value={p}>
              {monthLabel(p)}
            </option>
          ))}
        </optgroup>
        {withRanges &&
          rangeOptions().map((g) => (
            <optgroup key={g.group} label={g.group}>
              {g.options.map((o) => (
                <option key={o} value={o}>
                  {selectionLabel(o)}
                </option>
              ))}
            </optgroup>
          ))}
      </select>
    </label>
  );
}
