"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { api, type EmployeeRow } from "@/lib/api";

export default function EmployeesPage() {
  const [rows, setRows] = useState<EmployeeRow[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.employees().then(setRows).catch((e) => setError(e.message));
  }, []);

  const filtered = rows.filter((r) =>
    `${r.full_name} ${r.employee_id} ${r.region ?? ""} ${r.email ?? ""}`
      .toLowerCase()
      .includes(q.toLowerCase()),
  );

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">People</h1>
          <p className="text-sm text-ink-muted">
            Employee id is the only key used to match sales, targets and payouts.
          </p>
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, id or region"
          className="w-64 rounded-card border border-rule px-3 py-2 text-sm"
        />
      </header>

      {error && (
        <p role="alert" className="panel mt-6 bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}

      <section className="panel mt-6 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-canvas text-left text-micro text-ink-muted">
            <tr>
              <th className="p-3 font-medium">Name</th>
              <th className="p-3 font-medium">Email</th>
              <th className="p-3 font-medium">Role</th>
              <th className="p-3 font-medium">Region</th>
              <th className="p-3 font-medium">Zone</th>
              <th className="p-3 font-medium">Reports to</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.employee_id} className="border-t border-rule">
                <td className="p-3">
                  <div className="font-medium">{r.full_name}</div>
                  <div className="text-micro text-ink-faint">
                    {r.employee_id}
                    {!r.is_active && " · inactive"}
                  </div>
                </td>
                <td className="p-3">{r.email ?? "—"}</td>
                <td className="p-3">{r.designation ?? r.role}</td>
                <td className="p-3">{r.region ?? "—"}</td>
                <td className="p-3">{r.zone ?? "—"}</td>
                <td className="p-3">{r.submanager_id ?? r.rm_id ?? r.zm_id ?? "—"}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="p-8 text-center text-ink-muted">
                  No one matches that search.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </AppShell>
  );
}
