"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PeriodPicker } from "@/components/PeriodPicker";
import { api, type Rollup } from "@/lib/api";
import { count, monthLabel, percent, rupeesShort } from "@/lib/format";

/**
 * One page serves manager, RM, ZM and business head. The backend decides how
 * far the caller can see, so there is nothing role-specific in here beyond the
 * heading — and a BDE who reaches this URL sees exactly one row.
 */

function defaultPeriod(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

const GROUPINGS = [
  { key: "", label: "People" },
  { key: "region", label: "By region" },
  { key: "zone", label: "By zone" },
  { key: "submanager", label: "By sub-manager" },
];

export default function TeamPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [groupBy, setGroupBy] = useState("");
  const [data, setData] = useState<Rollup | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .rollup(period, groupBy || undefined)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [period, groupBy]);

  const s = data?.summary ?? {};

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My team</h1>
          <p className="text-sm text-ink-muted">{monthLabel(period)}</p>
        </div>
        <div className="flex items-center gap-2">
          <PeriodPicker value={period} onChange={setPeriod} />
          <a
            href={api.exportUrl("performance", period)}
            className="btn-quiet"
          >
            Export CSV
          </a>
        </div>
      </header>

      {error && (
        <p role="alert" className="panel mt-6 bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi label="Headcount" value={count(s.headcount as number)}
             sub="with sales this month" />
        <Kpi label="Target" value={rupeesShort(s.target_revenue as number)} />
        <Kpi label="Qualified revenue" value={rupeesShort(s.qualified_revenue as number)}
             sub={percent(s.achievement_pct as number) + " of target"} />
        <Kpi label="Qualification rate" value={percent(s.qualification_pct as number)}
             sub={rupeesShort(s.disqualified_revenue as number) + " disqualified"} />
        <Kpi label="Incentive liability" value={rupeesShort(s.incentive_liability as number)}
             sub={rupeesShort(s.net_payable as number) + " payable now"} />
      </div>

      <div className="mt-6 flex gap-1">
        {GROUPINGS.map((g) => (
          <button
            key={g.key}
            onClick={() => setGroupBy(g.key)}
            className={`rounded-card px-3 py-1.5 text-sm ${
              groupBy === g.key ? "bg-ink text-white" : "border border-rule bg-surface"
            }`}
          >
            {g.label}
          </button>
        ))}
      </div>

      {groupBy && data?.groups && (
        <section className="panel mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <th className="p-3 font-medium">Group</th>
                <th className="p-3 text-right font-medium">People</th>
                <th className="p-3 text-right font-medium">Target</th>
                <th className="p-3 text-right font-medium">Qualified</th>
                <th className="p-3 text-right font-medium">Disqualified</th>
                <th className="p-3 text-right font-medium">Incentive</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={String(g.group_key)} className="border-t border-rule">
                  <td className="p-3 font-medium">{String(g.group_key ?? "Unassigned")}</td>
                  <td className="p-3 text-right">{count(g.headcount as number)}</td>
                  <td className="p-3 text-right">{rupeesShort(g.target_revenue as number)}</td>
                  <td className="p-3 text-right text-qualified">
                    {rupeesShort(g.qualified_revenue as number)}
                  </td>
                  <td className="p-3 text-right text-disqualified">
                    {rupeesShort(g.disqualified_revenue as number)}
                  </td>
                  <td className="p-3 text-right">
                    {rupeesShort(g.incentive_liability as number)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {!groupBy && (
        <section className="panel mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <th className="p-3 font-medium">Name</th>
                <th className="p-3 font-medium">Region</th>
                <th className="p-3 text-right font-medium">Target</th>
                <th className="p-3 text-right font-medium">Achieved</th>
                <th className="p-3 text-right font-medium">Achievement</th>
                <th className="p-3 text-right font-medium">Qualified</th>
                <th className="p-3 text-right font-medium">Incentive</th>
              </tr>
            </thead>
            <tbody>
              {(data?.employees ?? []).map((e) => (
                <tr key={e.employee_id} className="border-t border-rule">
                  <td className="p-3">
                    <Link
                      href={`/team/${e.employee_id}?period=${period}`}
                      className="font-medium underline decoration-rule underline-offset-2"
                    >
                      {e.full_name}
                    </Link>
                    <div className="text-micro text-ink-faint">
                      {e.employee_id} · {e.designation ?? "—"}
                      {e.is_active === false && " · no sales"}
                    </div>
                  </td>
                  <td className="p-3">{e.region ?? "—"}</td>
                  <td className="p-3 text-right">{count(e.target_units)}</td>
                  <td className="p-3 text-right">{count(e.achieved_units)}</td>
                  <td className="p-3 text-right font-medium">{percent(e.base_pct)}</td>
                  <td className="p-3 text-right">{rupeesShort(e.qualified_revenue)}</td>
                  <td className="p-3 text-right">{rupeesShort(e.total_incentive)}</td>
                </tr>
              ))}
              {data && data.employees.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-ink-muted">
                    Nothing calculated for {monthLabel(period)} yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}
    </AppShell>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular">{value}</div>
      {sub && <div className="text-micro text-ink-faint">{sub}</div>}
    </div>
  );
}
