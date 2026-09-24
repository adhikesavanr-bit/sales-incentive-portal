"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * A small form in a modal, in place of window.prompt.
 *
 * window.prompt had two faults here. A chain of them could not be reviewed
 * or corrected before submitting. And dismissing one with Enter could land
 * as a click on the button that opened it, running the action twice. This
 * dialog submits once: the button locks while the action runs, errors are
 * shown inside it, and it only closes when the action succeeds.
 */

export type DialogField = {
  name: string;
  label: string;
  hint?: string;
  initial?: string;
  placeholder?: string;
  type?: "text" | "number" | "date";
  /** Minimum length after trimming. Defaults to 1 (required). */
  minLength?: number;
};

type Spec = {
  title: string;
  description?: string;
  fields: DialogField[];
  submitLabel?: string;
  /** Throw to keep the dialog open and show the message. */
  onSubmit: (values: Record<string, string>) => Promise<void>;
};

export function useFormDialog() {
  const [spec, setSpec] = useState<Spec | null>(null);
  const open = useCallback((s: Spec) => setSpec(s), []);
  const element = spec ? <FormDialog spec={spec} onClose={() => setSpec(null)} /> : null;
  return { open, element };
}

function FormDialog({ spec, onClose }: { spec: Spec; onClose: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(spec.fields.map((f) => [f.name, f.initial ?? ""])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busyRef.current) return;
    for (const f of spec.fields) {
      if ((values[f.name] ?? "").trim().length < (f.minLength ?? 1)) {
        setError(`${f.label} is required${(f.minLength ?? 1) > 1 ? ` (at least ${f.minLength} characters)` : ""}.`);
        return;
      }
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await spec.onSubmit(
        Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()])),
      );
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That could not be saved.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
    >
      <form
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-labelledby="form-dialog-title"
        className="panel w-full max-w-md p-6"
      >
        <h2 id="form-dialog-title" className="text-base font-semibold">{spec.title}</h2>
        {spec.description && (
          <p className="mt-1 text-sm text-ink-muted">{spec.description}</p>
        )}
        <div className="mt-4 space-y-3">
          {spec.fields.map((f, i) => (
            <label key={f.name} className="block">
              <span className="label">{f.label}</span>
              <input
                autoFocus={i === 0}
                type={f.type ?? "text"}
                step={f.type === "number" ? "any" : undefined}
                value={values[f.name]}
                placeholder={f.placeholder}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                className="inp"
              />
              {f.hint && <span className="mt-1 block text-micro text-ink-faint">{f.hint}</span>}
            </label>
          ))}
        </div>
        {error && (
          <p role="alert" className="mt-3 text-sm text-disqualified">{error}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="btn-quiet">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="btn-primary">
            {busy ? "Saving…" : spec.submitLabel ?? "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
