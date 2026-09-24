"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PeriodPicker } from "@/components/PeriodPicker";
import { api, type CouponRule, type SlabRow } from "@/lib/api";
import { count, monthLabel, percent } from "@/lib/format";

/**
 * The rules behind every number in the app, in one place.
 *
 * Two sets: coupon qualification thresholds, and achievement slabs. Both are
 * effective-dated — an edit writes a new version from a chosen date and leaves
 * history intact — so a month that has already been paid keeps the rules it
 * was paid under. The API refuses an effective date that lands in an approved
 * or locked month.
 */

const SCOPE_LABELS: Record<string, string> = {
  BDE_MONTHLY: "BDE — monthly",
  SUBMANAGER_MONTHLY: "Sub-manager (TM / SBM) — monthly",
  TEAM_OWNER_QUARTERLY: "Regional manager — quarterly",
  ZM_NINE_MONTH: "Zonal manager — nine months",
  FIRST_YEAR_MBBS_UNITS: "First-year MBBS incremental (by units)",
};

function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function firstOfNextMonth(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1, 1);
  return d.toISOString().slice(0, 10);
}

export default function RulesPage() {
  const [period, setPeriod] = useState(thisMonth());
  const [coupons, setCoupons] = useState<CouponRule[]>([]);
  const [scopes, setScopes] = useState<Record<string, SlabRow[]>>({});
  const [editable, setEditable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    setError(null);
    api.couponRules(period)
      .then((r) => { setCoupons(r.rules); setEditable(r.editable); })
      .catch((e) => setError(e.message));
    api.slabs(period).then((r) => setScopes(r.scopes)).catch(() => setScopes({}));
  }
  useEffect(load, [period]);

  async function editCoupon(rule: CouponRule) {
    const minSales = window.prompt(
      `${rule.group_size}: clubbed sales needed to qualify`,
      String(rule.min_sales),
    );
    if (minSales === null) return;
    const minOwn = window.prompt(
      `${rule.group_size}: sales the coupon must make on its own`,
      String(rule.min_own_sales),
    );
    if (minOwn === null) return;
    const from = window.prompt(
      "Effective from (YYYY-MM-DD). Months already approved or locked cannot be changed.",
      firstOfNextMonth(),
    );
    if (!from) return;
    const reason = window.prompt("Why is this changing?");
    if (!reason) return;

    try {
      await api.updateCouponRule({
        group_size: rule.group_size,
        required_sales: rule.required_sales,
        min_sales: Number(minSales),
        min_own_sales: Number(minOwn),
        effective_from: from,
        reason,
      });
      setNotice(`${rule.group_size} updated, effective ${from}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The rule could not be saved.");
    }
  }

  async function editSlab(scope: string, slab: SlabRow) {
    const rate = window.prompt(
      `Rate at ${percent(slab.threshold, 0)} and above, as a percentage`,
      String((slab.rate * 100).toFixed(3)),
    );
    if (rate === null) return;
    const from = window.prompt(
      "Effective from (YYYY-MM-DD)", firstOfNextMonth(),
    );
    if (!from) return;
    const reason = window.prompt("Why is this changing?");
    if (!reason) return;

    try {
      await api.updateSlab({
        scope,
        threshold: slab.threshold,
        rate: Number(rate) / 100,
        effective_from: from,
        reason,
      });
      setNotice(`${SCOPE_LABELS[scope] ?? scope} updated, effective ${from}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The slab could not be saved.");
    }
  }

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rules</h1>
          <p className="text-sm text-ink-muted">
            The thresholds and rates behind every figure, as they stood in{" "}
            {monthLabel(period)}.
          </p>
        </div>
        <PeriodPicker value={period} onChange={setPeriod} />
      </header>

      {error && (
        <p role="alert" className="panel mt-6 bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}
      {notice && (
        <p className="panel mt-6 bg-qualified-wash p-4 text-sm text-qualified">{notice}</p>
      )}

      <section className="panel mt-6 overflow-hidden">
        <div className="border-b border-rule p-4">
          <h2 className="text-sm font-semibold">Coupon qualification</h2>
          <p className="mt-1 text-sm text-ink-muted">
            A coupon qualifies when its clubbed sales reach the minimum, and it
            has made enough sales of its own for clubbing to carry it. The
            own-sales floor is waived when a foundation coupon was sold
            alongside.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <th className="p-3 font-medium">Group size</th>
                <th className="p-3 text-right font-medium">Coupon size</th>
                <th className="p-3 text-right font-medium">Clubbed sales to qualify</th>
                <th className="p-3 text-right font-medium">Utilisation</th>
                <th className="p-3 text-right font-medium">Own sales needed</th>
                <th className="p-3 font-medium">In force from</th>
                <th className="p-3" />
              </tr>
            </thead>
            <tbody>
              {coupons.map((r) => (
                <tr key={r.group_size} className="border-t border-rule">
                  <td className="p-3 font-medium">{r.group_size}</td>
                  <td className="p-3 text-right">{count(r.required_sales)}</td>
                  <td className="p-3 text-right font-medium">{count(r.min_sales)}</td>
                  <td className="p-3 text-right text-ink-muted">
                    {percent(r.min_sales / r.required_sales, 0)}
                  </td>
                  <td className="p-3 text-right font-medium">{count(r.min_own_sales)}</td>
                  <td className="p-3 text-ink-muted">{r.effective_from ?? "—"}</td>
                  <td className="p-3 text-right">
                    {editable && (
                      <button onClick={() => editCoupon(r)} className="text-sm underline">
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {coupons.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-ink-muted">
                    No coupon rules recorded for this period.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {Object.entries(scopes).map(([scope, slabs]) => (
        <section key={scope} className="panel mt-4 overflow-hidden">
          <div className="border-b border-rule p-4">
            <h2 className="text-sm font-semibold">{SCOPE_LABELS[scope] ?? scope}</h2>
            <p className="mt-1 text-sm text-ink-muted">
              The rate applies to the whole of qualified revenue once
              achievement reaches the threshold.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-canvas text-left text-micro text-ink-muted">
                <tr>
                  <th className="p-3 font-medium">Achievement at or above</th>
                  <th className="p-3 text-right font-medium">Rate</th>
                  <th className="p-3 font-medium">In force from</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {slabs.map((slab) => (
                  <tr key={`${scope}-${slab.threshold}`} className="border-t border-rule">
                    <td className="p-3">
                      {scope === "FIRST_YEAR_MBBS_UNITS"
                        ? `${count(slab.threshold)} units`
                        : percent(slab.threshold, 0)}
                    </td>
                    <td className="p-3 text-right font-medium">{percent(slab.rate, 2)}</td>
                    <td className="p-3 text-ink-muted">{slab.effective_from ?? "—"}</td>
                    <td className="p-3 text-right">
                      {editable && (
                        <button
                          onClick={() => editSlab(scope, slab)}
                          className="text-sm underline"
                        >
                          Edit
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      <p className="mt-6 text-sm text-ink-muted">
        Every change is versioned from the date you choose and written to the
        audit log with a reason. Months that are already approved or locked keep
        the rules they were calculated under; an edit dated inside one is
        refused.
      </p>
    </AppShell>
  );
}
