"use client";

import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";
export interface SortState<K extends string> {
  key: K;
  dir: SortDir;
}

/** Blanks last in either direction; numbers numerically; text A–Z ignoring case. */
export function sortRows<T, K extends string>(
  rows: T[],
  value: (row: T, key: K) => string | number | null | undefined,
  sort: SortState<K>,
): T[] {
  const factor = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = value(a, sort.key);
    const y = value(b, sort.key);
    const xBlank = x === null || x === undefined || x === "";
    const yBlank = y === null || y === undefined || y === "";
    if (xBlank || yBlank) return xBlank === yBlank ? 0 : xBlank ? 1 : -1;
    if (typeof x === "number" && typeof y === "number") return (x - y) * factor;
    return String(x).localeCompare(String(y), "en-IN", { sensitivity: "base" }) * factor;
  });
}

/**
 * Sort rows by one column. Blanks always go last, whichever way the column
 * is sorted, so "no target" never crowds the top of a list.
 */
export function useSort<T, K extends string>(
  rows: T[],
  value: (row: T, key: K) => string | number | null | undefined,
  initial: SortState<K>,
) {
  const [sort, setSort] = useState<SortState<K>>(initial);

  const sorted = useMemo(() => sortRows(rows, value, sort),
    // `value` is a pure accessor, so only the rows and the sort matter.
    [rows, sort]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Click once to sort by a column, again to reverse it. */
  function toggle(key: K, firstDir: SortDir = "desc") {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: firstDir }));
  }

  return { sorted, sort, toggle, setSort };
}

export function SortTh<K extends string>({
  label,
  column,
  sort,
  onSort,
  align = "left",
  firstDir = "desc",
  className = "p-3",
}: {
  label: string;
  column: K;
  sort: SortState<K>;
  onSort: (key: K, firstDir?: SortDir) => void;
  align?: "left" | "right";
  firstDir?: SortDir;
  className?: string;
}) {
  const active = sort.key === column;
  return (
    <th
      className={`${className} font-medium ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(column, firstDir)}
        className={`inline-flex items-center gap-1 hover:text-ink ${active ? "text-ink" : ""}`}
      >
        {label}
        <span aria-hidden className={`text-[10px] ${active ? "" : "opacity-30"}`}>
          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

/** The same box as the People page search, so the three pages match. */
export function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <input
      type="search"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label={placeholder}
      className="w-64 rounded-card border border-rule px-3 py-2 text-sm"
    />
  );
}

export function matches(q: string, ...fields: (string | null | undefined)[]): boolean {
  const needle = q.trim().toLowerCase();
  return !needle || fields.join(" ").toLowerCase().includes(needle);
}
