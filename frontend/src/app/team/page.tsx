"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { DownloadButton } from "@/components/DownloadButton";
import { PeriodPicker } from "@/components/PeriodPicker";
import {
  SearchBox, SortTh, matches, sortRows, useSort, type SortDir, type SortState,
} from "@/components/Sortable";
import { api, type EmployeeMetricRow, type Rollup } from "@/lib/api";
import { count, monthLabel, percent, rupeesShort, defaultPeriod } from "@/lib/format";

/**
 * One page serves manager, RM, ZM and business head. The backend decides how
 * far the caller can see, so there is nothing role-specific in here beyond the
 * heading — and a BDE who reaches this URL sees exactly one row.
 */


// Which field of a person's row each grouping keys on, to list a group's people.
const GROUP_FIELD: Record<string, keyof EmployeeMetricRow> = {
  region: "region",
  zone: "zone",
  submanager: "submanager_id",
};

const GROUPINGS = [
  { key: "", label: "People" },
  { key: "region", label: "By region" },
  { key: "zone", label: "By zone" },
  { key: "submanager", label: "By sub-manager" },
];

type PersonKey =
  | "name" | "region" | "target" | "achieved" | "achievement" | "qualified" | "disqualified" | "incentive";
type GroupKey = "group" | "people" | "target" | "qualified" | "disqualified" | "incentive";
type GroupRow = NonNullable<Rollup["groups"]>[number];

function personValue(e: EmployeeMetricRow, key: PersonKey) {
  switch (key) {
    case "name": return e.full_name;
    case "region": return e.region;
    case "target": return e.target_units;
    case "achieved": return e.achieved_units;
    case "achievement": return e.base_pct;
    case "qualified": return e.qualified_revenue;
    case "disqualified": return e.disqualified_revenue;
    case "incentive": return e.total_incentive;
  }
}

// A person's group (sub-manager) reads by name; the ID stays as the key.
function groupLabel(g: GroupRow) {
  if (g.group_key == null) return "Unassigned";
  return g.group_name || String(g.group_key);
}

function groupValue(g: GroupRow, key: GroupKey) {
  switch (key) {
    case "group": return g.group_key == null ? null : groupLabel(g);
    case "people": return g.headcount as number;
    case "target": return g.target_revenue as number;
    case "qualified": return g.qualified_revenue as number;
    case "disqualified": return g.disqualified_revenue as number;
    case "incentive": return g.incentive_liability as number;
  }
}

function personMatches(e: EmployeeMetricRow, q: string) {
  return matches(q, e.full_name, e.employee_id, e.designation, e.region, e.zone);
}

export default function TeamPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [groupBy, setGroupBy] = useState("");
  const [data, setData] = useState<Rollup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");

  useEffect(() => setOpen(new Set()), [groupBy, period]);

  function toggle(key: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function membersOf(key: unknown): EmployeeMetricRow[] {
    const field = GROUP_FIELD[groupBy];
    if (!field || !data) return [];
    return data.employees.filter((e) => (e[field] ?? null) === (key ?? null));
  }

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .rollup(period, groupBy || undefined)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [period, groupBy]);

  const s = data?.summary ?? {};

  const visiblePeople = (data?.employees ?? []).filter((e) => personMatches(e, q));
  const people = useSort<EmployeeMetricRow, PersonKey>(
    visiblePeople, personValue, { key: "achievement", dir: "desc" },
  );
  // While searching, a group stays if its name matches or anyone in it does.
  const visibleGroups = (data?.groups ?? []).filter(
    (g) => !q.trim() || matches(q, groupLabel(g), String(g.group_key ?? ""))
      || membersOf(g.group_key).some((e) => personMatches(e, q)),
  );
  const groups = useSort<GroupRow, GroupKey>(
    visibleGroups, groupValue, { key: "qualified", dir: "desc" },
  );
  const searching = !!q.trim();
  const groupMatchesByName = (g: GroupRow) =>
    matches(q, groupLabel(g), String(g.group_key ?? ""));

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My team</h1>
          <p className="text-sm text-ink-muted">{monthLabel(period)}</p>
        </div>
        <div className="flex items-center gap-2">
          <PeriodPicker value={period} onChange={setPeriod} />
          <DownloadButton onDownload={() => api.exportCsv("performance", period)}>
            Export CSV
          </DownloadButton>
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

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex gap-1">
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
        <SearchBox value={q} onChange={setQ} placeholder="Search name, ID, region or designation" />
      </div>

      {groupBy && data?.groups && (
        <section className="panel mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <SortTh label="Group" column="group" firstDir="asc" sort={groups.sort} onSort={groups.toggle} />
                <SortTh label="People" column="people" align="right" sort={groups.sort} onSort={groups.toggle} />
                <SortTh label="Target" column="target" align="right" sort={groups.sort} onSort={groups.toggle} />
                <SortTh label="Qualified" column="qualified" align="right" sort={groups.sort} onSort={groups.toggle} />
                <SortTh label="Disqualified" column="disqualified" align="right" sort={groups.sort} onSort={groups.toggle} />
                <SortTh label="Incentive" column="incentive" align="right" sort={groups.sort} onSort={groups.toggle} />
              </tr>
            </thead>
            <tbody>
              {groups.sorted.map((g) => {
                const key = String(g.group_key ?? "");
                // A search opens the groups it found people in.
                const expanded = open.has(key) || (searching && !groupMatchesByName(g));
                return (
                <Fragment key={key}>
                <tr
                  className="cursor-pointer border-t border-rule hover:bg-canvas"
                  onClick={() => toggle(key)}
                >
                  <td className="p-3 font-medium">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={(ev) => { ev.stopPropagation(); toggle(key); }}
                      className="flex items-center gap-2 text-left"
                    >
                      <span aria-hidden className="inline-block w-3 text-ink-faint">
                        {expanded ? "▾" : "▸"}
                      </span>
                      {groupLabel(g)}
                      {g.group_name && (
                        <span className="text-micro font-normal text-ink-faint">
                          {String(g.group_key)}
                        </span>
                      )}
                    </button>
                  </td>
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
                {expanded && (
                  <tr className="bg-canvas/60">
                    <td colSpan={6} className="px-3 pb-3 pt-0">
                      <GroupMembers
                        people={groupMatchesByName(g)
                          ? membersOf(g.group_key)
                          : membersOf(g.group_key).filter((e) => personMatches(e, q))}
                        period={period}
                        sort={people.sort}
                        onSort={people.toggle}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
                );
              })}
              {visibleGroups.length === 0 && data.groups.length > 0 && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-ink-muted">
                    No one matches that search.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {!groupBy && (
        <section className="panel mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <SortTh label="Name" column="name" firstDir="asc" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Region" column="region" firstDir="asc" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Target" column="target" align="right" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Achieved" column="achieved" align="right" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Achievement" column="achievement" align="right" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Qualified" column="qualified" align="right" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Disqualified" column="disqualified" align="right" sort={people.sort} onSort={people.toggle} />
                <SortTh label="Incentive" column="incentive" align="right" sort={people.sort} onSort={people.toggle} />
              </tr>
            </thead>
            <tbody>
              {people.sorted.map((e) => (
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
                  <td className="p-3 text-right text-disqualified">{rupeesShort(e.disqualified_revenue)}</td>
                  <td className="p-3 text-right">{rupeesShort(e.total_incentive)}</td>
                </tr>
              ))}
              {data && data.employees.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-ink-muted">
                    Nothing calculated for {monthLabel(period)} yet.
                  </td>
                </tr>
              )}
              {data && data.employees.length > 0 && visiblePeople.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-ink-muted">
                    No one matches that search.
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

function GroupMembers({
  people,
  period,
  sort,
  onSort,
}: {
  people: EmployeeMetricRow[];
  period: string;
  sort: SortState<PersonKey>;
  onSort: (key: PersonKey, firstDir?: SortDir) => void;
}) {
  const sorted = sortRows(people, personValue, sort);
  if (people.length === 0) {
    return <p className="p-3 text-sm text-ink-muted">No one in this group.</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-micro text-ink-muted">
        <tr>
          <SortTh label="Name" column="name" firstDir="asc" sort={sort} onSort={onSort}
                  className="py-2 pl-8 pr-3" />
          <SortTh label="Target" column="target" align="right" sort={sort} onSort={onSort} className="p-2" />
          <SortTh label="Achieved" column="achieved" align="right" sort={sort} onSort={onSort} className="p-2" />
          <SortTh label="Achievement" column="achievement" align="right" sort={sort} onSort={onSort} className="p-2" />
          <SortTh label="Qualified" column="qualified" align="right" sort={sort} onSort={onSort} className="p-2" />
          <SortTh label="Disqualified" column="disqualified" align="right" sort={sort} onSort={onSort} className="p-2" />
          <SortTh label="Incentive" column="incentive" align="right" sort={sort} onSort={onSort} className="p-2" />
        </tr>
      </thead>
      <tbody>
        {sorted.map((e) => (
          <tr key={e.employee_id} className="border-t border-rule">
            <td className="py-2 pl-8 pr-3">
              <Link
                href={`/team/${e.employee_id}?period=${period}`}
                className="font-medium underline decoration-rule underline-offset-2"
              >
                {e.full_name}
              </Link>
              <span className="ml-2 text-micro text-ink-faint">
                {e.employee_id} · {e.designation ?? "—"}
              </span>
            </td>
            <td className="p-2 text-right">{count(e.target_units)}</td>
            <td className="p-2 text-right">{count(e.achieved_units)}</td>
            <td className="p-2 text-right font-medium">{percent(e.base_pct)}</td>
            <td className="p-2 text-right">{rupeesShort(e.qualified_revenue)}</td>
            <td className="p-2 text-right text-disqualified">{rupeesShort(e.disqualified_revenue)}</td>
            <td className="p-2 text-right">{rupeesShort(e.total_incentive)}</td>
          </tr>
        ))}
      </tbody>
    </table>
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
