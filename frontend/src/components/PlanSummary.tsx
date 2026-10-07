"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

import { api, type PlanSummaryRow } from "@/lib/api";
import { count, percent, rupees } from "@/lib/format";
import { SortTh, sortRows, type SortState } from "@/components/Sortable";

type SortKey = "label" | "revenue" | "payments";

interface Node {
  key: string;
  label: string;
  sub?: string;
  payments: number;
  revenue: number;
  qualified: number;
  children: Node[];
}

// Plan > duration > region > BDE, the order of the Plan Wise Summary pivot.
// The BDE row carries the employee ID.
const LEVELS: ((r: PlanSummaryRow) => { key: string; label: string; sub?: string })[] = [
  (r) => ({ key: r.plan_title, label: r.plan_title }),
  (r) => ({
    key: String(r.plan_duration_in_month ?? ""),
    label: r.plan_duration_in_month ? `${r.plan_duration_in_month} months` : "Duration not set",
  }),
  (r) => ({ key: r.region, label: r.region }),
  (r) => ({ key: r.employee_id, label: r.bde_name ?? r.employee_id, sub: r.employee_id }),
];

function build(rows: PlanSummaryRow[]): Node[] {
  const roots: Node[] = [];
  for (const r of rows) {
    let level = roots;
    let path = "";
    LEVELS.forEach((pick) => {
      const { key, label, sub } = pick(r);
      path += `/${key}`;
      let node = level.find((n) => n.key === path);
      if (!node) {
        node = { key: path, label, sub, payments: 0, revenue: 0, qualified: 0, children: [] };
        level.push(node);
      }
      node.payments += r.payments;
      node.revenue += r.revenue;
      node.qualified += r.qualified_revenue;
      level = node.children;
    });
  }
  return roots;
}

/** Orders every level of the tree the same way, so a drill-down reads like its parent. */
function sortTree(nodes: Node[], sort: SortState<SortKey>): Node[] {
  return sortRows(nodes, (n, k) => n[k], sort).map((n) => ({
    ...n,
    children: sortTree(n.children, sort),
  }));
}

/** Plan-level summary with drill-down. Business heads and finance/super admins. */
export function PlanSummary({ period }: { period: string }) {
  const [rows, setRows] = useState<PlanSummaryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    setRows(null);
    setError(null);
    setOpen(new Set());
    api.myPlanSummary(period).then(setRows).catch((e) => setError(e.message));
  }, [period]);

  const [sort, setSort] = useState<SortState<SortKey>>({ key: "revenue", dir: "desc" });
  const onSort = (key: SortKey, firstDir: "asc" | "desc" = "desc") =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: firstDir }));

  const built = useMemo(() => build(rows ?? []), [rows]);
  const tree = useMemo(() => sortTree(built, sort), [built, sort]);
  const total = built.reduce((a, n) => a + n.revenue, 0);
  const totalPayments = built.reduce((a, n) => a + n.payments, 0);

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  const render = (n: Node, depth: number): React.ReactNode => {
    const expandable = n.children.length > 0;
    const isOpen = open.has(n.key);
    return (
      <Fragment key={n.key}>
        <tr className={`border-t border-rule ${depth === 0 ? "font-medium" : ""}`}>
          <td className="p-3" style={{ paddingLeft: `${0.75 + depth * 1.25}rem` }}>
            {expandable ? (
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => toggle(n.key)}
                className="flex items-center gap-2 text-left"
              >
                <span aria-hidden className="w-3 text-ink-faint">{isOpen ? "▾" : "▸"}</span>
                {n.label}
              </button>
            ) : (
              <span className="pl-5">
                {n.label}
                {n.sub && <span className="ml-2 text-micro text-ink-faint">{n.sub}</span>}
              </span>
            )}
          </td>
          <td className="p-3 text-right tabular">{rupees(n.revenue)}</td>
          <td className="p-3 text-right tabular">{count(n.payments)}</td>
          <td className="p-3 text-right tabular">{percent(total ? n.revenue / total : 0)}</td>
        </tr>
        {isOpen && n.children.map((c) => render(c, depth + 1))}
      </Fragment>
    );
  };

  return (
    <section className="panel mt-4 overflow-hidden">
      <div className="border-b border-rule p-4">
        <h2 className="text-sm font-semibold">Plan-wise summary</h2>
        <p className="text-micro text-ink-muted">
          Revenue excl. GST, qualified and disqualified together. Open a plan to drill into
          duration, region and BDE; click a heading to sort every level.
        </p>
      </div>
      {error && <p role="alert" className="p-4 text-sm text-disqualified">{error}</p>}
      {!error && !rows && <p className="p-8 text-center text-sm text-ink-muted">Loading…</p>}
      {rows && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-micro text-ink-muted">
              <tr>
                <SortTh label="Plan" column="label" sort={sort} onSort={onSort} firstDir="asc" />
                <SortTh label="Revenue (excl. GST)" column="revenue" sort={sort} onSort={onSort} align="right" />
                <SortTh label="Units" column="payments" sort={sort} onSort={onSort} align="right" />
                <SortTh label="Share" column="revenue" sort={sort} onSort={onSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {tree.map((n) => render(n, 0))}
              {tree.length === 0 && (
                <tr>
                  <td colSpan={4} className="p-8 text-center text-sm text-ink-muted">
                    No sales for this month.
                  </td>
                </tr>
              )}
              {tree.length > 0 && (
                <tr className="border-t-2 border-rule bg-canvas font-semibold">
                  <td className="p-3">Grand total</td>
                  <td className="p-3 text-right tabular">{rupees(total)}</td>
                  <td className="p-3 text-right tabular">{count(totalPayments)}</td>
                  <td className="p-3 text-right tabular">{percent(1)}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
