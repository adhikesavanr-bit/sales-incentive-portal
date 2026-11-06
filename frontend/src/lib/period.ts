"use client";

import { useCallback, useSyncExternalStore } from "react";

import { defaultPeriod, monthLabel } from "@/lib/format";

/**
 * The selected month, shared by every page. Picking Oct on My performance
 * keeps Oct on My team, Targets, Rules and Sales upload. Held in memory and
 * mirrored to sessionStorage so it also survives a reload within the tab.
 */
const KEY = "incentive.period";
const listeners = new Set<() => void>();
let current: string | null = null;

function read(): string {
  if (current) return current;
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved && /^(\d{4}-\d{2}|(Q[1-4]|FY):\d{4})$/.test(saved)) return (current = saved);
  } catch {
    /* storage unavailable: fall back to the default */
  }
  return (current = defaultPeriod());
}

function write(period: string) {
  if (period === current) return;
  current = period;
  try {
    sessionStorage.setItem(KEY, period);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/**
 * A selection is a month ("2026-10") or a range over an Apr-Mar financial year:
 * "Q1:2026" (Apr-Jun), "Q2:2026" (Jul-Sep), "Q3:2026" (Oct-Dec),
 * "Q4:2026" (Jan-Mar 2027) or "FY:2026" (Apr 2026 - Mar 2027). The year is the
 * one the financial year starts in.
 */
export type Selection = string;

const RANGE = /^(Q[1-4]|FY):(\d{4})$/;
const QUARTER_NAME = { Q1: "AMJ", Q2: "JAS", Q3: "OND", Q4: "JFM" } as const;
const QUARTER_START = { Q1: 4, Q2: 7, Q3: 10, Q4: 1 } as const;

const pad = (n: number) => String(n).padStart(2, "0");
const fyShort = (y: number) => `${String(y).slice(2)}-${String(y + 1).slice(2)}`;

export function isRange(sel: Selection): boolean {
  return RANGE.test(sel);
}

export function rangeBounds(sel: Selection): { start: string; end: string } | null {
  const m = RANGE.exec(sel);
  if (!m) return null;
  const y = Number(m[2]);
  if (m[1] === "FY") return { start: `${y}-04`, end: `${y + 1}-03` };
  const q = m[1] as keyof typeof QUARTER_START;
  const startMonth = QUARTER_START[q];
  const startYear = q === "Q4" ? y + 1 : y;
  const endMonth = startMonth + 2;
  return { start: `${startYear}-${pad(startMonth)}`, end: `${startYear}-${pad(endMonth)}` };
}

export function selectionLabel(sel: Selection): string {
  const m = RANGE.exec(sel);
  if (!m) return monthLabel(sel);
  const y = Number(m[2]);
  return m[1] === "FY"
    ? `FY ${fyShort(y)}`
    : `${m[1]} · ${QUARTER_NAME[m[1] as keyof typeof QUARTER_NAME]} ${fyShort(y)}`;
}

/** The financial year (start year) a month falls in. */
function fyOf(month: string): number {
  const [y, m] = month.split("-").map(Number);
  return m >= 4 ? y : y - 1;
}

/** Quarters and the full year for the current and previous financial year. */
export function rangeOptions(): { group: string; options: Selection[] }[] {
  const cur = fyOf(defaultPeriod());
  return [cur, cur - 1].map((y) => ({
    group: `FY ${fyShort(y)}`,
    options: [`Q1:${y}`, `Q2:${y}`, `Q3:${y}`, `Q4:${y}`, `FY:${y}`],
  }));
}

/** A single month for pages that cannot show a range: a range maps to its
 *  last month that has had time to be calculated. */
export function resolveMonth(sel: Selection): string {
  const b = rangeBounds(sel);
  if (!b) return sel;
  const latest = defaultPeriod();
  return b.end < latest ? b.end : b.start > latest ? b.start : latest;
}

export function usePeriod(): [Selection, (sel: Selection) => void] {
  const sel = useSyncExternalStore(subscribe, read, defaultPeriod);
  return [sel, useCallback(write, [])];
}

/** Same shared selection, for pages that work on one month at a time. */
export function useMonth(): [string, (period: string) => void] {
  const [sel, set] = usePeriod();
  return [resolveMonth(sel), set];
}

/** Quarter and year views are for business heads, Finance and super admins. */
const RANGE_ROLES = new Set(["BUSINESS_HEAD", "FINANCE_ADMIN", "SUPER_ADMIN"]);
export const canUseRanges = (role: string | undefined | null): boolean =>
  !!role && RANGE_ROLES.has(role);

/** The shared selection for a caller: anyone else sees a range as its last month. */
export function usePeriodFor(role: string | undefined | null): [Selection, (sel: Selection) => void] {
  const [sel, set] = usePeriod();
  return [canUseRanges(role) ? sel : resolveMonth(sel), set];
}
