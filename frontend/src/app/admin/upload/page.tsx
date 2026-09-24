"use client";

import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { DownloadButton } from "@/components/DownloadButton";
import { PeriodPicker } from "@/components/PeriodPicker";
import { api, type MonthStatus, type RecalcResult, type UploadSummary } from "@/lib/api";
import { count, monthLabel, rupeesShort } from "@/lib/format";

function defaultPeriod(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function UploadPage() {
  const [period, setPeriod] = useState(defaultPeriod());
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<UploadSummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recalc, setRecalc] = useState<RecalcResult | null>(null);
  const [status, setStatus] = useState<MonthStatus | null>(null);

  async function validate() {
    if (!file) return;
    setBusy("Reading the file and running the coupon rules…");
    setError(null);
    setNotice(null);
    try {
      setSummary(await api.validateUpload(file, period));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed.");
      setSummary(null);
    } finally {
      setBusy(null);
    }
  }

  async function runImport(withErrors: boolean) {
    if (!summary) return;
    setBusy("Importing…");
    setError(null);
    try {
      const res = await api.importBatch(summary.batch_id, withErrors);
      setNotice(`Imported ${count(res.rows_imported)} rows into ${monthLabel(period)}.`);
      setSummary(null);
      setFile(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed.");
    } finally {
      setBusy(null);
    }
  }

  async function recalculate() {
    const reason = window.prompt("Why are you recalculating this month?");
    if (!reason) return;
    setBusy("Recalculating…");
    setError(null);
    try {
      setRecalc(await api.recalculate(period, reason));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Recalculation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function changeStatus(to: MonthStatus) {
    const reason = window.prompt(`Why are you moving ${monthLabel(period)} to ${to}?`);
    if (!reason) return;
    try {
      const res = await api.setPeriodStatus(period, to, reason);
      setStatus(res.status);
      setNotice(`${monthLabel(period)} is now ${res.status.replace(/_/g, " ").toLowerCase()}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the month status.");
    }
  }

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sales upload</h1>
          <p className="text-sm text-ink-muted">
            Load a month of sales, check it, then publish the incentive.
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

      <section className="panel mt-6 p-6">
        <h2 className="text-sm font-semibold">1. Choose a file</h2>
        <p className="mt-1 text-sm text-ink-muted">
          A .csv or .xlsx export with the standard sales columns. Nothing is
          written until you confirm the import.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,.xls"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setSummary(null);
            }}
            className="text-sm file:mr-3 file:rounded-card file:border file:border-rule
                       file:bg-surface file:px-3 file:py-2 file:text-sm"
          />
          <button onClick={validate} disabled={!file || !!busy} className="btn-primary">
            Check this file
          </button>
        </div>
        {busy && <p className="mt-3 text-sm text-ink-muted">{busy}</p>}
      </section>

      {summary && (
        <section className="panel mt-4 p-6">
          <h2 className="text-sm font-semibold">2. Review before importing</h2>
          <dl className="mt-4 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <Fig label="Rows" value={count(summary.total_rows)} />
            <Fig label="Valid" value={count(summary.valid_rows)} tone="qualified" />
            <Fig label="With errors" value={count(summary.invalid_rows)}
                 tone={summary.invalid_rows ? "disqualified" : undefined} />
            <Fig label="Duplicates" value={count(summary.duplicate_rows)} />
            <Fig label="Unknown coupons" value={count(summary.unknown_coupons)}
                 tone={summary.unknown_coupons ? "disqualified" : undefined} />
            <Fig label="Qualified" value={rupeesShort(summary.qualified_revenue)} />
          </dl>

          {summary.unknown_coupons > 0 && (
            <p className="mt-4 rounded-card bg-disqualified-wash p-3 text-sm text-disqualified">
              {count(summary.unknown_coupons)} sales use a coupon that is not in
              the coupon master. They stay unassigned — add the coupons, then
              check the file again.
            </p>
          )}

          {summary.errors.length > 0 && (
            <div className="mt-4">
              <h3 className="text-micro text-ink-muted">First errors</h3>
              <ul className="mt-2 space-y-1 text-sm">
                {summary.errors.slice(0, 8).map((e, i) => (
                  <li key={i} className="text-ink-muted">
                    Row {e.row_number} · {e.field} — {e.message}
                  </li>
                ))}
              </ul>
              <div className="mt-3">
                <DownloadButton
                  onDownload={() => api.downloadUploadErrors(summary.batch_id)}
                  className="text-sm underline"
                >
                  Download the full error list
                </DownloadButton>
              </div>
            </div>
          )}

          <div className="mt-6 flex gap-3">
            <button
              onClick={() => runImport(false)}
              disabled={!!busy || summary.validation_status === "FAILED"}
              className="btn-primary"
            >
              Import {count(summary.valid_rows)} rows
            </button>
            {summary.invalid_rows > 0 && (
              <button onClick={() => runImport(true)} disabled={!!busy} className="btn-quiet">
                Import the valid rows only
              </button>
            )}
          </div>
        </section>
      )}

      <section className="panel mt-4 p-6">
        <h2 className="text-sm font-semibold">3. Calculate and publish</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Recalculating writes a new version; earlier versions stay readable.
          Locking freezes the month.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <button onClick={recalculate} disabled={!!busy} className="btn-primary">
            Recalculate {monthLabel(period)}
          </button>
          <button onClick={() => changeStatus("UNDER_REVIEW")} className="btn-quiet">
            Send for review
          </button>
          <button onClick={() => changeStatus("APPROVED")} className="btn-quiet">
            Approve
          </button>
          <button onClick={() => changeStatus("LOCKED")} className="btn-quiet">
            Lock
          </button>
        </div>
        {status && <p className="mt-3 text-sm text-ink-muted">Status: {status}</p>}

        {recalc && (
          <dl className="mt-6 grid gap-4 border-t border-rule pt-6 sm:grid-cols-4">
            <Fig label="Version" value={`v${recalc.calculation_version}`} />
            <Fig label="Employees" value={count(recalc.employees)} />
            <Fig label="Incentive earned" value={rupeesShort(recalc.total_incentive)} />
            <Fig label="Payable now" value={rupeesShort(recalc.net_payable)} />
          </dl>
        )}
      </section>
    </AppShell>
  );
}

function Fig({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "qualified" | "disqualified";
}) {
  const colour =
    tone === "qualified" ? "text-qualified" : tone === "disqualified" ? "text-disqualified" : "";
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`mt-0.5 text-lg font-semibold tabular ${colour}`}>{value}</dd>
    </div>
  );
}
