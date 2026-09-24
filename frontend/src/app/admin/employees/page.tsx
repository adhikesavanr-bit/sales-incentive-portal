"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { useFormDialog } from "@/components/FormDialog";
import { api, startViewAs, type EmployeeRow, type RoleOption } from "@/lib/api";

/**
 * The people master. Everything downstream keys off this list: targets, team
 * roll-ups, sign-in and coupon attribution.
 *
 * Leavers are deactivated with an exit date rather than deleted, because their
 * past months still need an owner for every sale.
 */

const BLANK: EmployeeRow = {
  employee_id: "", full_name: "", email: "", initial: "", role: "BDE",
  designation: "BDE", region: "", zone: "", submanager_id: null, rm_id: null,
  zm_id: null, is_active: true, exit_date: null,
};

export default function PeoplePage() {
  const [rows, setRows] = useState<EmployeeRow[]>([]);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<EmployeeRow | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [canManage, setCanManage] = useState(false);
  // View-as is for super admins only; the API enforces it, this just hides it.
  const [me, setMe] = useState<{ id: string; superAdmin: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The reason is a field on the form, not a window.prompt: dismissing a
  // prompt with Enter can re-click the focused button, which submitted twice.
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const dialog = useFormDialog();

  function load() {
    api.employees(showInactive).then(setRows).catch((e) => setError(e.message));
  }
  useEffect(load, [showInactive]);

  useEffect(() => {
    api.me()
      .then((m) => {
        setCanManage(m.permissions.includes("MANAGE_EMPLOYEES"));
        setMe({ id: m.employee_id, superAdmin: m.role === "SUPER_ADMIN" && !m.impersonated_by });
      })
      .catch(() => setCanManage(false));
    if (new URLSearchParams(window.location.search).get("view_as") === "ended") {
      setNotice("View-as ended after 30 minutes. You are back in your own account.");
    }
    api.assignableRoles().then(setRoles).catch(() => setRoles([]));
  }, []);

  function startEditing(row: EmployeeRow, asNew: boolean) {
    setEditing({ ...row });
    setIsNew(asNew);
    setReason("");
    setError(null);
    setNotice(null);
  }

  async function save() {
    if (!editing || saving) return;
    if (!editing.employee_id.trim() || !editing.full_name.trim()) {
      setError("Employee ID and name are required.");
      return;
    }
    if (reason.trim().length < 3) {
      setError("Say briefly why — it goes in the audit log.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (isNew) {
        await api.createEmployee(editing, reason.trim());
        setNotice(`${editing.full_name} added. They now appear in Targets and My team.`);
      } else {
        await api.updateEmployee(editing.employee_id, editing, reason.trim());
        setNotice(`${editing.full_name} updated.`);
      }
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  async function viewAs(row: EmployeeRow) {
    setError(null);
    try {
      await startViewAs(row.employee_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start view-as.");
    }
  }

  function deactivate(row: EmployeeRow) {
    dialog.open({
      title: `Mark ${row.full_name} as left`,
      description: "Their record is kept, so the months they were paid for stay intact.",
      submitLabel: "Mark as left",
      fields: [
        { name: "exit", label: "Last working day", type: "date",
          initial: new Date().toISOString().slice(0, 10) },
        { name: "reason", label: "Reason", minLength: 3, placeholder: "Resigned" },
      ],
      onSubmit: async ({ exit, reason }) => {
        await api.deactivateEmployee(row.employee_id, exit, reason);
        setNotice(`${row.full_name} marked as left on ${exit}. Past months are unchanged.`);
        load();
      },
    });
  }

  const filtered = rows.filter((r) =>
    `${r.full_name} ${r.employee_id} ${r.region ?? ""} ${r.email ?? ""} ${r.initial ?? ""}`
      .toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <AppShell>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">People</h1>
          <p className="text-sm text-ink-muted">
            The master list. Everyone here appears in Targets and in their
            manager&rsquo;s team.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, ID, initial or region"
            className="w-64 rounded-card border border-rule px-3 py-2 text-sm"
          />
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            <input type="checkbox" checked={showInactive}
                   onChange={(e) => setShowInactive(e.target.checked)} />
            Show leavers
          </label>
          {canManage && (
            <button onClick={() => startEditing(BLANK, true)}
                    className="btn-primary">
              Add person
            </button>
          )}
        </div>
      </header>

      {error && (
        <p role="alert" className="panel mt-6 bg-disqualified-wash p-4 text-sm text-disqualified">
          {error}
        </p>
      )}
      {notice && (
        <p className="panel mt-6 bg-qualified-wash p-4 text-sm text-qualified">{notice}</p>
      )}

      {editing && (
        <section className="panel mt-6 p-6">
          <h2 className="text-sm font-semibold">
            {isNew ? "Add a person" : `Edit ${editing.full_name}`}
          </h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Employee ID" hint="The key everything joins on">
              <input value={editing.employee_id} disabled={!isNew}
                     onChange={(e) => setEditing({ ...editing, employee_id: e.target.value })}
                     className="inp disabled:bg-canvas disabled:text-ink-muted"
                     placeholder="NHP999" />
            </Field>
            <Field label="Name">
              <input value={editing.full_name}
                     onChange={(e) => setEditing({ ...editing, full_name: e.target.value })}
                     className="inp" />
            </Field>
            <Field label="Email" hint="Must match their company account to sign in">
              <input value={editing.email ?? ""}
                     onChange={(e) => setEditing({ ...editing, email: e.target.value })}
                     className="inp" placeholder="name@marrowmed.com" />
            </Field>
            <Field label="Coupon agent" hint="The initial that prefixes their coupons">
              <input value={editing.initial ?? ""}
                     onChange={(e) => setEditing({ ...editing, initial: e.target.value.toUpperCase() })}
                     className="inp" placeholder="PND" />
            </Field>
            <Field label="Designation" hint="As it appears in the incentive report">
              <input value={editing.designation ?? ""}
                     onChange={(e) => setEditing({ ...editing, designation: e.target.value })}
                     className="inp" placeholder="BDE / SBM / RM" />
            </Field>
            <Field label="Region">
              <input value={editing.region ?? ""}
                     onChange={(e) => setEditing({ ...editing, region: e.target.value })}
                     className="inp" placeholder="R1-Ajeet" />
            </Field>
            <Field label="Zone">
              <input value={editing.zone ?? ""}
                     onChange={(e) => setEditing({ ...editing, zone: e.target.value })}
                     className="inp" />
            </Field>
            <Field label="Access" hint="What they can see and do in this app">
              <select value={editing.role}
                      onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                      className="inp">
                {roles.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Reports to" hint="Employee ID of their manager">
              <input value={editing.rm_id ?? ""}
                     onChange={(e) => setEditing({ ...editing, rm_id: e.target.value || null })}
                     className="inp" placeholder="NHP212" />
            </Field>
          </div>
          <div className="mt-4 max-w-xl">
            <Field label="Reason" hint="Recorded in the audit log with the change">
              <input value={reason} onChange={(e) => setReason(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") save(); }}
                     className="inp"
                     placeholder={isNew ? "New joiner, R1" : "Promoted to RM"} />
            </Field>
          </div>
          <div className="mt-5 flex gap-3">
            <button onClick={save} disabled={saving} className="btn-primary disabled:opacity-60">
              {saving ? "Saving…" : isNew ? "Add person" : "Save changes"}
            </button>
            <button onClick={() => setEditing(null)} className="btn-quiet">Cancel</button>
          </div>
        </section>
      )}

      <section className="panel mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-canvas text-left text-micro text-ink-muted">
            <tr>
              <th className="p-3 font-medium">Name</th>
              <th className="p-3 font-medium">Email</th>
              <th className="p-3 font-medium">Coupon agent</th>
              <th className="p-3 font-medium">Designation</th>
              <th className="p-3 font-medium">Region</th>
              <th className="p-3 font-medium">Access</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.employee_id}
                  className={`border-t border-rule ${r.is_active ? "" : "opacity-60"}`}>
                <td className="p-3">
                  <div className="font-medium">{r.full_name}</div>
                  <div className="text-micro text-ink-faint">
                    {r.employee_id}{!r.is_active && ` · left ${r.exit_date ?? ""}`}
                  </div>
                </td>
                <td className="p-3">{r.email ?? "—"}</td>
                <td className="p-3">{r.initial ?? "—"}</td>
                <td className="p-3">{r.designation ?? "—"}</td>
                <td className="p-3">{r.region ?? "—"}</td>
                <td className="p-3 text-ink-muted">{r.role.replace(/_/g, " ").toLowerCase()}</td>
                <td className="p-3 text-right whitespace-nowrap">
                  {canManage && (
                    <>
                      <button onClick={() => startEditing(r, false)}
                              className="text-sm underline">Edit</button>
                      {me?.superAdmin && r.is_active && r.employee_id !== me.id && (
                        <button onClick={() => viewAs(r)}
                                title="See the app exactly as this person does. Read-only, 30 minutes."
                                className="ml-3 text-sm underline">
                          View as
                        </button>
                      )}
                      {r.is_active && (
                        <button onClick={() => deactivate(r)}
                                className="ml-3 text-sm text-ink-muted underline">
                          Mark as left
                        </button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={7} className="p-8 text-center text-ink-muted">
                No one matches that search.
              </td></tr>
            )}
          </tbody>
        </table>
      </section>

      {dialog.element}

      <p className="mt-6 text-sm text-ink-muted">
        People who leave are marked with an exit date rather than removed, so
        the months they were paid for stay intact. Every change is written to
        the audit log with a reason.
      </p>
    </AppShell>
  );
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-micro text-ink-faint">{hint}</span>}
    </label>
  );
}
