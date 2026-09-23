"use client";

import { rupees, rupeesShort } from "@/lib/format";

/**
 * The first thing a BDE sees.
 *
 * The scheme caps a month's payout at Rs.2,00,000 and defers the rest to year
 * end, so a single "incentive earned" figure would be misleading on exactly
 * the months people care most about. This splits the bar into what lands in
 * this month's payroll and what is held back.
 */
export function PayoutHeadline({
  total,
  netPayable,
  accumulation,
  cap = 200000,
}: {
  total: number;
  netPayable: number;
  accumulation: number;
  cap?: number;
}) {
  const paidShare = total > 0 ? netPayable / total : 0;

  return (
    <section className="panel p-6 sm:p-8">
      <div className="label">Incentive earned this month</div>
      <div className="mt-1 text-display font-semibold tabular text-teal-deep">
        {rupees(total)}
      </div>

      {accumulation > 0 ? (
        <>
          <div className="mt-6 flex h-2.5 overflow-hidden rounded-card">
            <div
              className="bg-teal"
              style={{ width: `${paidShare * 100}%` }}
            />
            <div
              className="bg-deferred"
              style={{ width: `${(1 - paidShare) * 100}%` }}
            />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-4">
            <div>
              <dt className="flex items-center gap-2 text-micro text-ink-muted">
                <span className="h-2 w-2 rounded-full bg-teal" />
                Paid with this month&rsquo;s salary
              </dt>
              <dd className="mt-1 text-xl font-semibold tabular">
                {rupees(netPayable)}
              </dd>
            </div>
            <div>
              <dt className="flex items-center gap-2 text-micro text-ink-muted">
                <span className="h-2 w-2 rounded-full bg-deferred" />
                Held to year end
              </dt>
              <dd className="mt-1 text-xl font-semibold tabular">
                {rupees(accumulation)}
              </dd>
            </div>
          </dl>
          <p className="mt-4 border-t border-rule pt-4 text-sm text-ink-muted">
            Policy pays at most {rupeesShort(cap)} in any one month. The balance
            accumulates and is released at the end of the financial year.
          </p>
        </>
      ) : (
        <p className="mt-4 text-sm text-ink-muted">
          Paid in full with this month&rsquo;s salary.
        </p>
      )}
    </section>
  );
}
