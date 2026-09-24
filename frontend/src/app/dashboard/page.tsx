"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AppShell } from "@/components/AppShell";
import { DownloadButton } from "@/components/DownloadButton";
import { PayoutHeadline } from "@/components/PayoutHeadline";
import { PeriodPicker } from "@/components/PeriodPicker";
import { SlabRuler } from "@/components/SlabRuler";
import { api, type Breakdown, type Transaction } from "@/lib/api";
import { count, monthLabel, percent, rupees, rupeesShort } from "@/lib/format";

// Mirrors Policy!L12:M16. Displayed only — the amount always comes from the API.
const BDE_SLABS = [
  { from: 0, rate: 0 },
  { from: 0.5, rate: 0.005 },
  { from: 0.8, rate: 0.01 },
  { from: 1.0, rate: 0.0225 },
  { from: 1.3, rate: 0.03 },
];

function defaultPeriod(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function DashboardPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [data, setData] = useState<Breakdown | null>(null);
  const [sales, setSales] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"ALL" | "QUALIFIED" | "DISQUALIFIED">("ALL");

  useEffect(() => {
    setData(null);
    setError(null);
    api.myDashboard(period).then(setData).catch((e) => setError(e.message));
    api.mySales(period).then(setSales).catch(() => setSales([]));
  }, [period]);

  const rows = sales.filter((s) => filter === "ALL" || s.status === filter);

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My performance</h1>
          <p className="text-sm text-ink-muted">{monthLabel(period)}</p>
        </div>
        <div className="flex items-start gap-2">
          <PeriodPicker value={period} onChange={setPeriod} />
          {data && !data.status && (
            <DownloadButton onDownload={() => api.exportStatementPdf(period)}>
              Export PDF
            </DownloadButton>
          )}
        </div>
      </header>

      {error && (
        <p role="alert" className="mt-6 panel bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}

      {data?.status && (
        <div className="panel mt-6 p-10 text-center text-sm text-ink-muted">
          {data.message}
        </div>
      )}

      {data && !data.status && (
        <>
          <div className="mt-6 grid gap-4 lg:grid-cols-5">
            <div className="lg:col-span-3">
              <PayoutHeadline
                total={data.total_incentive}
                netPayable={data.net_payable}
                accumulation={data.accumulation}
              />
            </div>
            <div className="panel p-6 lg:col-span-2">
              <SlabRuler
                value={data.base_pct}
                slabs={BDE_SLABS}
                currentRate={data.bde_rate}
              />
            </div>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Target" value={`${count(data.target_units)} units`}
                  sub={rupeesShort(data.target_revenue)} />
            <Stat label="Sold" value={`${count(data.gross_units)} units`}
                  sub={rupeesShort(data.gross_revenue)} />
            <Stat label="Qualified" value={rupeesShort(data.qualified_revenue)}
                  sub={`${count(data.achieved_units)} units counted`} tone="qualified" />
            <Stat label="Disqualified" value={rupeesShort(data.disqualified_revenue)}
                  sub={`${count(data.disqualified_units)} sales`} tone="disqualified" />
          </div>

          <section className="panel mt-4 p-6">
            <h2 className="text-sm font-semibold">How this was calculated</h2>
            <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2">
              <Line term="Qualified revenue (excl. GST)" value={rupees(data.qualified_revenue)} />
              <Line term="Target revenue" value={rupees(data.target_revenue)} />
              <Line term="Revenue achievement" value={percent(data.revenue_pct)} />
              <Line term="Unit achievement" value={percent(data.unit_pct)} />
              <Line term="Your ARPU" value={rupees(data.arpu)} />
              <Line term="Achievement used" value={percent(data.base_pct)} />
              <Line term="Incentive rate" value={percent(data.bde_rate, 2)} />
              <Line term="Incentive" value={rupees(data.bde_incentive)} />
            </dl>
            <p className="mt-4 border-t border-rule pt-4 text-sm text-ink-muted">
              {data.arpu_rule_applied}
            </p>
          </section>

          {data.trend && data.trend.length > 0 && (
            <section className="panel mt-4 p-6">
              <h2 className="text-sm font-semibold">Daily sales</h2>
              <div className="mt-4 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.trend}>
                    <CartesianGrid stroke="#DDE1E6" vertical={false} />
                    <XAxis dataKey="day" tick={{ fontSize: 11 }} stroke="#8C95A3"
                           tickFormatter={(d: string) => d.slice(8)} />
                    <YAxis tick={{ fontSize: 11 }} stroke="#8C95A3"
                           tickFormatter={(v: number) => `${v / 1000}k`} />
                    <Tooltip formatter={(v: number) => rupees(v)} />
                    <Bar dataKey="qualified_revenue" name="Qualified" fill="#2F7D5E" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          <section className="panel mt-4 overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule p-4">
              <h2 className="text-sm font-semibold">Sales ({count(rows.length)})</h2>
              <div className="flex gap-1">
                {(["ALL", "QUALIFIED", "DISQUALIFIED"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`rounded-card px-3 py-1.5 text-micro ${
                      filter === f ? "bg-ink text-white" : "border border-rule"
                    }`}
                  >
                    {f === "ALL" ? "All" : f === "QUALIFIED" ? "Qualified" : "Disqualified"}
                  </button>
                ))}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-canvas text-left text-micro text-ink-muted">
                  <tr>
                    <th className="p-3 font-medium">Date</th>
                    <th className="p-3 font-medium">Plan</th>
                    <th className="p-3 font-medium">College</th>
                    <th className="p-3 font-medium">Coupon</th>
                    <th className="p-3 text-right font-medium">Net</th>
                    <th className="p-3 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => (
                    <tr key={t.payment_id} className="border-t border-rule align-top">
                      <td className="p-3 whitespace-nowrap">
                        {t.payment_date_ist?.slice(0, 10)}
                      </td>
                      <td className="p-3">{t.plan_title ?? "—"}</td>
                      <td className="p-3 max-w-[18rem] truncate">{t.college_name ?? "—"}</td>
                      <td className="p-3">{t.coupon ?? "—"}</td>
                      <td className="p-3 text-right">{rupees(t.net_amount)}</td>
                      <td className="p-3">
                        {t.status === "QUALIFIED" ? (
                          <span className="rounded-card bg-qualified-wash px-2 py-0.5 text-micro text-qualified">
                            Qualified
                          </span>
                        ) : (
                          <div className="max-w-xs">
                            <span className="rounded-card bg-disqualified-wash px-2 py-0.5 text-micro text-disqualified">
                              Disqualified
                            </span>
                            {t.reason_detail && (
                              <p className="mt-1 text-micro text-ink-muted">
                                {t.reason_detail}
                              </p>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-8 text-center text-sm text-ink-muted">
                        No sales to show for this filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </AppShell>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "qualified" | "disqualified";
}) {
  const colour =
    tone === "qualified" ? "text-qualified" : tone === "disqualified" ? "text-disqualified" : "";
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular ${colour}`}>{value}</div>
      {sub && <div className="text-micro text-ink-faint">{sub}</div>}
    </div>
  );
}

function Line({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-rule pb-2">
      <dt className="text-sm text-ink-muted">{term}</dt>
      <dd className="text-sm font-medium tabular">{value}</dd>
    </div>
  );
}
