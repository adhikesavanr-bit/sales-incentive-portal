"use client";

import { useEffect, useRef, useState } from "react";

import { useFormDialog } from "@/components/FormDialog";
import {
  api,
  type CouponAgentsDiff,
  type CouponAgentsSummary,
  type CouponImport,
} from "@/lib/api";
import { count, monthLabel } from "@/lib/format";

/**
 * The month's coupons, from the coupon consumption report.
 *
 * The coupon agent list decides which coupons are field coupons and who owns
 * each one, so it is shown alongside and can be replaced here too.
 */
export function CouponStep({ period, step }: { period: string; step: number }) {
  const [agents, setAgents] = useState<CouponAgentsSummary | null>(null);
  const [agentFile, setAgentFile] = useState<File | null>(null);
  const [agentDiff, setAgentDiff] = useState<CouponAgentsDiff | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [check, setCheck] = useState<CouponImport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const reportInput = useRef<HTMLInputElement>(null);
  const agentInput = useRef<HTMLInputElement>(null);
  const dialog = useFormDialog();

  function loadAgents() {
    api.couponAgents().then(setAgents).catch(() => setAgents(null));
  }
  useEffect(loadAgents, []);
  useEffect(() => { setCheck(null); setFile(null); }, [period]);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function checkReport(f: File) {
    setFile(f);
    setCheck(null);
    const out = await run("Reading the coupon report…", () => api.importCoupons(f, period));
    if (out) setCheck(out);
    else setFile(null);
    if (reportInput.current) reportInput.current.value = "";
  }

  function saveReport() {
    if (!file || !check) return;
    dialog.open({
      title: `Save ${count(check.included)} coupons for ${monthLabel(period)}`,
      description: "Replaces any coupons uploaded earlier for this month. Other months are unchanged.",
      submitLabel: "Save coupons",
      fields: [{ name: "reason", label: "Why are you uploading this?", minLength: 3,
                 initial: `${monthLabel(period)} coupon consumption report` }],
      onSubmit: async ({ reason }) => {
        const out = await api.importCoupons(file, period, true, reason);
        setCheck(null);
        setFile(null);
        setNotice(`${count(out.written ?? 0)} coupons saved for ${monthLabel(period)}. ` +
          "Recalculate the month to use them.");
      },
    });
  }

  async function checkAgents(f: File) {
    setAgentFile(f);
    setAgentDiff(null);
    const out = await run("Reading the agent list…", () => api.replaceCouponAgents(f));
    if (out) setAgentDiff(out);
    else setAgentFile(null);
    if (agentInput.current) agentInput.current.value = "";
  }

  function saveAgents() {
    if (!agentFile || !agentDiff) return;
    dialog.open({
      title: `Replace the agent list (${agentDiff.agents} agents)`,
      description: "Used for every coupon report uploaded from now on. Coupons already saved are unchanged.",
      submitLabel: "Replace list",
      fields: [{ name: "reason", label: "Why is the list changing?", minLength: 3 }],
      onSubmit: async ({ reason }) => {
        await api.replaceCouponAgents(agentFile, true, reason);
        setAgentDiff(null);
        setAgentFile(null);
        setNotice("Agent list replaced.");
        loadAgents();
      },
    });
  }

  const excluded = check ? Object.entries(check.excluded_by_code) : [];

  return (
    <section className="panel mt-4 p-6">
      <h2 className="text-sm font-semibold">{step}. Coupons for the month</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Upload the <code>coupon_consumption_report_…csv</code> for {monthLabel(period)}.
        Coupons whose sales-master code is in the agent list are kept, with the owner
        and zone from the list and the group size from the discount group. Every
        other code is left out, as in the Coupon Working sheet. Nothing is saved
        until you confirm.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={() => reportInput.current?.click()}
          disabled={!!busy || !agents?.agents}
          className="btn-primary"
        >
          Upload coupon report
        </button>
        <input ref={reportInput} type="file" accept=".csv,.xlsx" className="hidden"
               onChange={(e) => { const f = e.target.files?.[0]; if (f) checkReport(f); }} />
        <span className="text-sm text-ink-muted">
          Agent list:{" "}
          {agents === null ? "…" : agents.agents === 0
            ? <span className="text-disqualified">not loaded yet — upload it first</span>
            : `${agents.agents} agents${agents.updated_by ? `, updated by ${agents.updated_by}` : ""}`}
          {" · "}
          <button onClick={() => agentInput.current?.click()} disabled={!!busy}
                  className="underline">
            {agents?.agents ? "Replace" : "Upload"} Coupon_Agent Details
          </button>
          <input ref={agentInput} type="file" accept=".csv,.xlsx" className="hidden"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) checkAgents(f); }} />
        </span>
      </div>

      {busy && <p className="mt-3 text-sm text-ink-muted">{busy}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-disqualified">{error}</p>}
      {notice && <p className="mt-3 text-sm text-qualified">{notice}</p>}

      {agentDiff && (
        <div className="mt-4 rounded-card border border-rule p-4 text-sm">
          <p>
            <span className="font-medium">{agentFile?.name}</span>: {agentDiff.agents} agents ·{" "}
            {agentDiff.added.length} new · {agentDiff.removed.length} removed ·{" "}
            {agentDiff.changed.length} with a new owner or zone
          </p>
          {[["New", agentDiff.added], ["Removed", agentDiff.removed],
            ["Changed", agentDiff.changed]].map(([label, codes]) =>
            (codes as string[]).length > 0 && (
              <p key={label as string} className="mt-1 text-ink-muted">
                {label as string}: {(codes as string[]).join(", ")}
              </p>
            ))}
          <div className="mt-3 flex gap-3">
            <button onClick={saveAgents} className="btn-primary">Replace agent list</button>
            <button onClick={() => { setAgentDiff(null); setAgentFile(null); }}
                    className="btn-quiet">Discard</button>
          </div>
        </div>
      )}

      {check && (
        <div className="mt-4 border-t border-rule pt-4">
          <dl className="grid gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <Fig label="Coupons in file" value={count(check.total)} />
            <Fig label="Field coupons kept" value={count(check.included)} tone="qualified" />
            <Fig label="Left out" value={count(check.excluded)} />
            <Fig label="Owners" value={count(check.owners)} />
            <Fig label="Errors" value={count(check.errors.length)}
                 tone={check.errors.length ? "disqualified" : undefined} />
          </dl>
          <p className="mt-3 text-sm text-ink-muted">
            By group size:{" "}
            {Object.entries(check.by_group_size).map(([g, n]) => `${g} ${count(n)}`).join(" · ")}
          </p>
          {excluded.length > 0 && (
            <p className="mt-1 text-sm text-ink-muted">
              Left out (code not in the agent list):{" "}
              {excluded.map(([c, n]) => `${c} ${n}`).join(" · ")}
            </p>
          )}
          {check.warnings.map((w) => (
            <p key={w} className="mt-2 rounded-card bg-amber-50 p-2 text-sm text-amber-900">{w}</p>
          ))}
          {check.errors.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-disqualified">
              {check.errors.slice(0, 8).map((e) => (
                <li key={e.line}>Row {e.line} · {e.message}</li>
              ))}
            </ul>
          )}
          <div className="mt-4 flex gap-3">
            <button onClick={saveReport}
                    disabled={check.errors.length > 0 || check.included === 0}
                    className="btn-primary">
              Save {count(check.included)} coupons
            </button>
            <button onClick={() => { setCheck(null); setFile(null); }} className="btn-quiet">
              Discard
            </button>
          </div>
        </div>
      )}
      {dialog.element}
    </section>
  );
}

function Fig({ label, value, tone }: {
  label: string; value: string; tone?: "qualified" | "disqualified";
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
