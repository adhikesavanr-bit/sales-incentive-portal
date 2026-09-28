"use client";

import { useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { useFormDialog } from "@/components/FormDialog";
import { PeriodPicker } from "@/components/PeriodPicker";
import { SearchBox, matches } from "@/components/Sortable";
import { api, cachedMe, type BulkTargetResult, type Me, type TargetRow } from "@/lib/api";
import { count, monthLabel, rupeesShort, defaultPeriod } from "@/lib/format";

const ARPU = 33000;

function csvCell(v: unknown): string {
  const t = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
}

/** Everyone on the page with their current target, ready to fill in and upload. */
function downloadTemplate(rows: TargetRow[], period: string) {
  const lines = [
    "employee_id,full_name,region,target_units,winner_units",
    ...rows.map((r) => [r.employee_id, r.full_name, r.region, r.target_units, ""]
      .map(csvCell).join(",")),
  ];
  const url = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `targets-${period}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function TargetsPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [rows, setRows] = useState<TargetRow[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [bulk, setBulk] = useState<BulkTargetResult | null>(null);
  const [checking, setChecking] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const dialog = useFormDialog();
  const [me, setMe] = useState<Me | null>(() => cachedMe());
  useEffect(() => { api.me().then(setMe).catch(() => {}); }, []);
  // Only admins set targets; everyone else, and view-as, sees them read-only.
  const canEdit = !!me && !me.impersonated_by && me.permissions.includes("PROPOSE_TARGETS");

  function load() {
    api.targets(period).then(setRows).catch((e) => setError(e.message));
  }
  useEffect(load, [period]);
  // A preview belongs to the month it was checked against.
  useEffect(() => { setBulk(null); setBulkFile(null); }, [period]);

  const filtered = rows.filter((r) =>
    matches(q, r.full_name, r.employee_id, r.region, r.designation));

  async function checkFile(file: File) {
    setError(null);
    setNotice(null);
    setBulk(null);
    setBulkFile(file);
    setChecking(true);
    try {
      setBulk(await api.bulkTargets(file, period));
    } catch (e) {
      setBulkFile(null);
      setError((e as Error).message);
    } finally {
      setChecking(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function confirmBulk() {
    if (!bulk || !bulkFile) return;
    const n = bulk.counts.change;
    dialog.open({
      title: `Save ${n} target${n === 1 ? "" : "s"}`,
      description: `Targets for ${monthLabel(period)} from ${bulkFile.name}.`,
      submitLabel: "Save targets",
      fields: [{ name: "reason", label: "Why are these changing?", minLength: 3 }],
      onSubmit: async ({ reason }) => {
        const out = await api.bulkTargets(bulkFile, period, true, reason);
        setBulk(null);
        setBulkFile(null);
        setNotice(`${out.written ?? 0} target${out.written === 1 ? "" : "s"} saved for ${monthLabel(period)}.`);
        load();
      },
    });
  }

  function save(row: TargetRow) {
    const units = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(units) || units < 0) {
      setError("Enter a target as a whole number of units.");
      return;
    }
    setError(null);
    dialog.open({
      title: `${row.full_name ?? row.employee_id}: ${units} units`,
      description: `Target for ${monthLabel(period)}.`,
      submitLabel: "Save target",
      fields: [{ name: "reason", label: "Why is this changing?", minLength: 3 }],
      onSubmit: async ({ reason }) => {
        await api.saveTarget({
          employee_id: row.employee_id,
          period,
          target_units: units,
          reason,
        });
        setEditing(null);
        load();
      },
    });
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
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={q} onChange={setQ} placeholder="Search name, ID or region" />
          <PeriodPicker value={period} onChange={setPeriod} />
        </div>
      </header>

      {canEdit && (
        <section className="panel mt-6 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Bulk upload</h2>
              <p className="mt-1 text-sm text-ink-muted">
                Download the template, fill in <code>target_units</code> (and{" "}
                <code>winner_units</code> if it is not the base × 1.3), then upload it as
                CSV or Excel. Blank rows are left as they are. You see every change
                before anything is saved.
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => downloadTemplate(rows, period)}
                className="rounded-card border border-rule bg-surface px-3 py-2 text-sm"
              >
                Download template
              </button>
              <button
                onClick={() => fileInput.current?.click()}
                disabled={checking}
                className="rounded-card bg-ink px-3 py-2 text-sm text-white disabled:opacity-50"
              >
                {checking ? "Checking…" : "Upload file"}
              </button>
              <input
                ref={fileInput}
                type="file"
                accept=".csv,.xlsx,.xlsm,.xls"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) checkFile(f); }}
              />
            </div>
          </div>

          {bulk && (
            <div className="mt-4 border-t border-rule pt-4">
              <p className="text-sm">
                <span className="font-medium">{bulkFile?.name}</span>:{" "}
                {bulk.counts.change} to change · {bulk.counts.unchanged} unchanged ·{" "}
                {bulk.counts.skipped} blank ·{" "}
                <span className={bulk.counts.error ? "text-disqualified" : ""}>
                  {bulk.counts.error} with errors
                </span>
              </p>
              {bulk.rows.some((r) => r.status === "error" || r.status === "change") && (
                <div className="mt-3 max-h-80 overflow-auto rounded-card border border-rule">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-canvas text-left text-micro text-ink-muted">
                      <tr>
                        <th className="p-2 font-medium">Row</th>
                        <th className="p-2 font-medium">Employee</th>
                        <th className="p-2 text-right font-medium">Now</th>
                        <th className="p-2 text-right font-medium">New</th>
                        <th className="p-2 font-medium">Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bulk.rows
                        .filter((r) => r.status === "error" || r.status === "change")
                        .sort((a, b) => Number(b.status === "error") - Number(a.status === "error"))
                        .map((r) => (
                          <tr key={r.line} className="border-t border-rule">
                            <td className="p-2 text-ink-faint">{r.line}</td>
                            <td className="p-2">
                              {r.full_name ?? r.employee_id}
                              {r.full_name && (
                                <span className="ml-1 text-micro text-ink-faint">{r.employee_id}</span>
                              )}
                            </td>
                            <td className="p-2 text-right">{count(r.old_target_units)}</td>
                            <td className="p-2 text-right font-medium">{count(r.target_units)}</td>
                            <td className={`p-2 ${r.error ? "text-disqualified" : "text-ink-muted"}`}>
                              {r.error ?? "Will change"}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  onClick={confirmBulk}
                  disabled={bulk.counts.error > 0 || bulk.counts.change === 0}
                  className="rounded-card bg-ink px-3 py-2 text-sm text-white disabled:opacity-50"
                >
                  Save {bulk.counts.change} change{bulk.counts.change === 1 ? "" : "s"}
                </button>
                <button
                  onClick={() => { setBulk(null); setBulkFile(null); }}
                  className="text-sm text-ink-muted underline"
                >
                  Discard
                </button>
                {bulk.counts.error > 0 && (
                  <span className="text-sm text-disqualified">
                    Fix the rows with errors and upload again. Nothing is saved while any row has an error.
                  </span>
                )}
              </div>
            </div>
          )}
        </section>
      )}

      {error && (
        <p role="alert" className="panel mt-6 bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}
      {notice && (
        <p className="panel mt-6 bg-qualified-wash p-4 text-sm text-qualified">{notice}</p>
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
            {filtered.map((r) => (
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
                  ) : canEdit && (
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
            {rows.length > 0 && filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="p-8 text-center text-ink-muted">
                  No one matches that search.
                </td>
              </tr>
            )}
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
      {dialog.element}
    </AppShell>
  );
}
