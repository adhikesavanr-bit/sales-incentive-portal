"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Breakdown } from "@/lib/api";
import { rupees } from "@/lib/format";

/**
 * The daily sales bars. Its own module so the dashboard can load recharts —
 * the largest dependency in the bundle — after the numbers are on screen.
 */
export default function DailySalesChart({ data }: { data: NonNullable<Breakdown["trend"]> }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data}>
        <CartesianGrid stroke="#DDE1E6" vertical={false} />
        <XAxis dataKey="day" tick={{ fontSize: 11 }} stroke="#8C95A3"
               tickFormatter={(d: string) => d.slice(8)} />
        <YAxis tick={{ fontSize: 11 }} stroke="#8C95A3"
               tickFormatter={(v: number) => `${v / 1000}k`} />
        <Tooltip formatter={(v: number) => rupees(v)} />
        <Bar dataKey="qualified_revenue" name="Qualified" fill="#2F7D5E" />
      </BarChart>
    </ResponsiveContainer>
  );
}
