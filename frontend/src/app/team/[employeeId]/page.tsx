"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { DownloadButton } from "@/components/DownloadButton";
import { api, type Breakdown, type Transaction } from "@/lib/api";
import { count, monthLabel, percent, rupees } from "@/lib/format";

/** Drill-down: team -> employee -> transaction. */
export default function EmployeeDetailPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const period = useSearchParams().get("period") ?? "";
  const [data, setData] = useState<Breakdown | null>(null);
  const [sales, setSales] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!period) return;
    api.employeeDashboard(employeeId, period).then(setData).catch((e) => setError(e.message));
    api.employeeSales(employeeId, period).then(setSales).catch(() => setSales([]));
  }, [employeeId, period]);

  if (error) {
    return (
      <AppShell>
        <p role="alert" className="panel bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
        <Link href="/team" className="mt-4 inline-block text-sm underline">
          Back to my team
        </Link>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <Link href={`/team?period=${period}`} className="text-sm text-ink-muted underline">
        Back to my team
      </Link>
      <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{employeeId}</h1>
          <p className="text-sm text-ink-muted">{period && monthLabel(period)}</p>
        </div>
        {data && !data.status && (
          <DownloadButton onDownload={() => api.exportStatementPdf(period, employeeId)}>
            Export PDF
          </DownloadButton>
        )}
      </header>

      {data?.status && (
        <div className="panel mt-6 p-10 text-center text-sm text-ink-muted">
          {data.message}
        </div>
      )}

      {data && !data.status && (
        <>
          <dl className="panel mt-6 grid gap-x-8 gap-y-3 p-6 sm:grid-cols-2">
            <Row term="Target units" value={count(data.target_units)} />
            <Row term="Achieved units" value={count(data.achieved_units)} />
            <Row term="Target revenue" value={rupees(data.target_revenue)} />
            <Row term="Qualified revenue" value={rupees(data.qualified_revenue)} />
            <Row term="Disqualified revenue" value={rupees(data.disqualified_revenue)} />
            <Row term="ARPU" value={rupees(data.arpu)} />
            <Row term="Achievement used" value={percent(data.base_pct)} />
            <Row term="Rate" value={percent(data.bde_rate, 2)} />
            <Row term="Incentive earned" value={rupees(data.total_incentive)} />
            <Row term="Payable this month" value={rupees(data.net_payable)} />
          </dl>
          <p className="mt-3 text-sm text-ink-muted">{data.arpu_rule_applied}</p>
        </>
      )}

      <section className="panel mt-6 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-canvas text-left text-micro text-ink-muted">
            <tr>
              <th className="p-3 font-medium">Date</th>
              <th className="p-3 font-medium">Coupon</th>
              <th className="p-3 font-medium">Plan</th>
              <th className="p-3 text-right font-medium">Net</th>
              <th className="p-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {sales.map((t) => (
              <tr key={t.payment_id} className="border-t border-rule align-top">
                <td className="p-3 whitespace-nowrap">{t.payment_date_ist?.slice(0, 10)}</td>
                <td className="p-3">{t.coupon ?? "—"}</td>
                <td className="p-3">{t.plan_title ?? "—"}</td>
                <td className="p-3 text-right">{rupees(t.net_amount)}</td>
                <td className="p-3">
                  {t.status === "QUALIFIED" ? (
                    <span className="text-qualified">Qualified</span>
                  ) : (
                    <span className="text-disqualified">
                      {t.reason_detail ?? "Disqualified"}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </AppShell>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-rule pb-2">
      <dt className="text-sm text-ink-muted">{term}</dt>
      <dd className="text-sm font-medium tabular">{value}</dd>
    </div>
  );
}
