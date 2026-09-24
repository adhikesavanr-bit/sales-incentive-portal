"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PeriodPicker } from "@/components/PeriodPicker";
import { api, type TargetRow } from "@/lib/api";
import { count, monthLabel, rupeesShort } from "@/lib/format";

const ARPU = 33000;

function defaultPeriod(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function TargetsPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [rows, setRows] = useState<TargetRow[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.targets(period).then(setRows).catch((e) => setError(e.message));
  }
  useEffect(load, [period]);

  async function save(row: TargetRow) {
    const units = Number(draft);
    if (!Number.isFinite(units) || units < 0) {
      setError("Enter a target as a whole number of units.");
      return;
    }
    const reason = window.prompt(`Why is ${row.full_name}'s target changing?`);
    if (!reason) return;
    try {
      await api.saveTarget({
        employee_id: row.employee_id,
        period,
        target_units: units,
        reason,
      });
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The target could not be saved.");
    }
  }

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Targets</h1>
          <p className="text-sm text-ink-muted">
            Winner units are the base times 1.3. Revenue target is units times{" "}
            {rupeesShort(ARPU)}.
          </p>
        </div>
        <PeriodPicker value={period} onChange={setPeriod} />
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
              <th className="p-3 font-medium">Region</th>
              <th className="p-3 text-right font-medium">Base units</th>
              <th className="p-3 text-right font-medium">Winner units</th>
              <th className="p-3 text-right font-medium">Revenue target</th>
              <th className="p-3 font-medium">Status</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.employee_id} className="border-t border-rule">
                <td className="p-3">
                  <div className="font-medium">{r.full_name ?? r.employee_id}</div>
                  <div className="text-micro text-ink-faint">{r.employee_id}</div>
                </td>
                <td className="p-3">{r.region ?? "—"}</td>
                <td className="p-3 text-right">
                  {editing === r.employee_id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      className="w-24 rounded-card border border-rule px-2 py-1 text-right"
                    />
                  ) : (
                    count(r.target_units)
                  )}
                </td>
                <td className="p-3 text-right">{count(r.winner_units)}</td>
                <td className="p-3 text-right">
                  {r.target_units === null ? "—" : rupeesShort(r.target_units * ARPU)}
                </td>
                <td className="p-3">
                  <span
                    className={`rounded-card px-2 py-0.5 text-micro ${
                      r.status === "APPROVED"
                        ? "bg-qualified-wash text-qualified"
                        : "bg-canvas text-ink-muted"
                    }`}
                  >
                    {r.status ? r.status.toLowerCase() : "not set"}
                  </span>
                </td>
                <td className="p-3 text-right">
                  {editing === r.employee_id ? (
                    <div className="flex justify-end gap-2">
                      <button onClick={() => save(r)} className="text-sm underline">
                        Save
                      </button>
                      <button
                        onClick={() => setEditing(null)}
                        className="text-sm text-ink-muted underline"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => {
                        setEditing(r.employee_id);
                        setDraft(r.target_units === null ? "" : String(r.target_units));
                      }}
                      className="text-sm underline"
                    >
                      Edit
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="p-8 text-center text-ink-muted">
                  No targets set for {monthLabel(period)} yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </AppShell>
  );
}
