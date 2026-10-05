"use client";

import { monthLabel } from "@/lib/format";

/**
 * Shown to Finance and admins while they review a month the sales line cannot
 * see yet, so nobody mistakes a draft for what their team is looking at.
 */
export function UnpublishedBanner({
  period,
  monthStatus,
}: {
  period: string;
  monthStatus?: string;
}) {
  const state = (monthStatus ?? "OPEN").replace(/_/g, " ").toLowerCase();
  return (
    <p role="status" className="panel mt-6 bg-disqualified-wash p-4 text-sm">
      <span className="font-semibold text-disqualified">Not published.</span>{" "}
      {monthLabel(period)} is {state}. Only Finance and admins can see these figures; the
      team sees them once the month is approved.
    </p>
  );
}
