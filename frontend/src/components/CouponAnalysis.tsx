"use client";

import { useEffect, useState } from "react";

import { type CouponVerdict } from "@/lib/api";
import { count } from "@/lib/format";

/**
 * Each coupon's verdict for the month — the "Coupon Analysis" block of the
 * per-person workbook: college, coupon, group size, sales, qualified.
 */
export function CouponAnalysis({
  load,
  period,
}: {
  load: () => Promise<CouponVerdict[]>;
  period: string;
}) {
  const [rows, setRows] = useState<CouponVerdict[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    setError(null);
    load().then(setRows).catch((e) => setError(e.message));
    // `load` is a fresh closure each render; the period is what changes it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  const qualified = rows?.filter((r) => r.qualified).length ?? 0;
  const units = rows?.reduce((n, r) => n + (r.total ?? 0), 0) ?? 0;

  return (
    <section className="panel mt-4 overflow-hidden">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule p-4">
        <h2 className="text-sm font-semibold">Coupon analysis</h2>
        {rows && rows.length > 0 && (
          <p className="text-micro text-ink-muted">
            {count(rows.length)} coupons · {count(qualified)} qualified ·{" "}
            {count(units)} sales
          </p>
        )}
      </div>
      {error && <p className="p-4 text-sm text-disqualified">{error}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-canvas text-left text-micro text-ink-muted">
            <tr>
              <th className="p-3 font-medium">College ID</th>
              <th className="p-3 font-medium">Coupon</th>
              <th className="p-3 font-medium">Group</th>
              <th className="p-3 text-right font-medium">Sales</th>
              <th className="p-3 text-right font-medium">Club sales</th>
              <th className="p-3 font-medium">Qualified</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((r) => (
              <tr key={r.coupon_signature} className="border-t border-rule align-top">
                <td className="p-3 font-mono text-micro text-ink-muted">{r.college_id ?? "—"}</td>
                <td className="p-3 font-medium">
                  {r.coupon_code}
                  {r.is_foundation && (
                    <span className="ml-2 text-micro text-ink-faint">foundation</span>
                  )}
                </td>
                <td className="p-3">{r.group_size ?? "—"}</td>
                <td className="p-3 text-right">{count(r.total)}</td>
                <td className="p-3 text-right text-ink-muted">
                  {count(r.club_sales)}
                  {r.min_sales != null && (
                    <span className="text-ink-faint"> / {count(r.min_sales)}</span>
                  )}
                </td>
                <td className="p-3">
                  {r.qualified ? (
                    <span className="rounded-card bg-qualified-wash px-2 py-0.5 text-micro text-qualified">
                      Yes
                    </span>
                  ) : (
                    <span className="rounded-card bg-disqualified-wash px-2 py-0.5 text-micro text-disqualified">
                      No
                    </span>
                  )}
                  {r.overridden && r.override_reason && (
                    <p className="mt-1 text-micro text-ink-muted">Override: {r.override_reason}</p>
                  )}
                </td>
              </tr>
            ))}
            {rows && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="p-8 text-center text-sm text-ink-muted">
                  No coupon sales this month.
                </td>
              </tr>
            )}
            {!rows && !error && (
              <tr>
                <td colSpan={6} className="p-8 text-center text-sm text-ink-muted">Loading…</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
