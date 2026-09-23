"use client";

import { monthLabel, recentPeriods } from "@/lib/format";

export function PeriodPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (period: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm">
      <span className="sr-only">Month</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-card border border-rule bg-surface px-3 py-2 text-sm"
      >
        {recentPeriods().map((p) => (
          <option key={p} value={p}>
            {monthLabel(p)}
          </option>
        ))}
      </select>
    </label>
  );
}
