/**
 * API client. The browser holds a session token and nothing else — no GCP
 * credential, project id or dataset name ever reaches this layer.
 */

// Same origin. Next.js rewrites /api/* to the backend at runtime — see
// next.config.mjs. Nothing about the API location is compiled into this bundle.
const BASE = "";
const TOKEN_KEY = "incentive_portal_token";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.sessionStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Your session expired. Sign in again.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* the body was not JSON; keep the generic message */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  login: (idToken: string) =>
    request<{ access_token: string; expires_in: number }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ id_token: idToken }),
    }),

  config: () =>
    request<{ google_client_id: string; allowed_email_domains: string[] }>(
      "/api/auth/config",
    ),

  devLogin: () =>
    request<{ access_token: string; expires_in: number }>("/api/auth/dev-login", {
      method: "POST",
    }),

  me: () => request<Me>("/api/auth/me"),

  myDashboard: (period: string) =>
    request<Breakdown>(`/api/me/dashboard?period=${period}`),

  mySales: (period: string, limit = 200, offset = 0) =>
    request<Transaction[]>(
      `/api/me/sales?period=${period}&limit=${limit}&offset=${offset}`,
    ),

  employeeDashboard: (id: string, period: string) =>
    request<Breakdown>(`/api/employees/${id}/dashboard?period=${period}`),

  employeeSales: (id: string, period: string) =>
    request<Transaction[]>(`/api/employees/${id}/sales?period=${period}`),

  rollup: (period: string, groupBy?: string) =>
    request<Rollup>(
      `/api/rollup/dashboard?period=${period}${groupBy ? `&group_by=${groupBy}` : ""}`,
    ),

  periodStatus: (period: string) =>
    request<{ period: string; status: MonthStatus }>(`/api/period/${period}/status`),

  setPeriodStatus: (period: string, to: MonthStatus, reason: string) =>
    request<{ status: MonthStatus }>(
      `/api/period/${period}/status?to=${to}&reason=${encodeURIComponent(reason)}`,
      { method: "POST" },
    ),

  validateUpload: (file: File, period: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("period", period);
    return request<UploadSummary>("/api/sales/validate", { method: "POST", body });
  },

  importBatch: (batchId: string, withErrors = false) =>
    request<{ rows_imported: number }>(
      `/api/sales/import/${batchId}?confirm_with_errors=${withErrors}`,
      { method: "POST" },
    ),

  recalculate: (period: string, reason: string) =>
    request<RecalcResult>(
      `/api/incentive/calculate?period=${period}&reason=${encodeURIComponent(reason)}`,
      { method: "POST" },
    ),

  targets: (period: string) => request<TargetRow[]>(`/api/targets?period=${period}`),

  saveTarget: (body: {
    employee_id: string;
    period: string;
    target_units: number;
    reason: string;
  }) => request<TargetRow>("/api/targets", { method: "POST", body: JSON.stringify(body) }),

  employees: () => request<EmployeeRow[]>("/api/employees"),

  rules: () => request<RuleRow[]>("/api/incentive/rules"),

  audit: (limit = 200) => request<AuditRow[]>(`/api/audit?limit=${limit}`),

  exportUrl: (report: string, period: string) =>
    `/api/export/${report}?period=${period}`,
};

// --- types ----------------------------------------------------------------
export type MonthStatus = "OPEN" | "UNDER_REVIEW" | "APPROVED" | "LOCKED";

export interface Me {
  employee_id: string;
  full_name: string;
  email: string;
  role: string;
  region: string | null;
  zone: string | null;
  vertical: string | null;
  permissions: string[];
}

export interface Breakdown {
  status?: "NOT_CALCULATED" | "NO_SALES";
  message?: string;
  month_status?: MonthStatus;
  employee_id: string;
  period: string;
  designation: string | null;
  region: string | null;
  target_units: number;
  achieved_units: number;
  gross_units: number;
  disqualified_units: number;
  foundation_units: number;
  unit_pct: number;
  target_revenue: number;
  gross_revenue: number;
  qualified_revenue: number;
  disqualified_revenue: number;
  revenue_pct: number;
  arpu: number;
  arpu_rule_applied: string;
  base_pct: number;
  bde_rate: number;
  bde_incentive: number;
  submanager_incentive: number;
  total_incentive: number;
  accumulation: number;
  net_payable: number;
  is_active: boolean;
  trend?: { day: string; units: number; net_revenue: number; qualified_revenue: number }[];
  plan_mix?: { plan_title: string; units: number; net_revenue: number }[];
}

export interface Transaction {
  payment_date_ist: string;
  payment_id: string;
  invoice_id: string | null;
  plan_title: string | null;
  plan_duration_in_month: number | null;
  college_name: string | null;
  coupon: string | null;
  paid_amount: number;
  net_amount: number;
  status: "QUALIFIED" | "DISQUALIFIED" | "UNATTRIBUTED";
  disqualification_reason: string | null;
  reason_detail: string | null;
}

export interface Rollup {
  period: string;
  scope: string;
  summary: Record<string, number>;
  employees: EmployeeMetricRow[];
  groups?: { group_key: string; [k: string]: unknown }[];
}

export interface EmployeeMetricRow {
  employee_id: string;
  full_name: string;
  designation: string | null;
  region: string | null;
  zone: string | null;
  target_units: number | null;
  achieved_units: number | null;
  unit_pct: number | null;
  qualified_revenue: number | null;
  disqualified_revenue: number | null;
  revenue_pct: number | null;
  base_pct: number | null;
  arpu: number | null;
  total_incentive: number | null;
  net_payable: number | null;
  accumulation: number | null;
  is_active: boolean | null;
}

export interface UploadSummary {
  batch_id: string;
  filename: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  unknown_coupons: number;
  qualified_revenue: number;
  disqualified_revenue: number;
  validation_status: string;
  errors: { row_number: number; field: string; error_code: string; message: string }[];
}

export interface RecalcResult {
  period: string;
  calculation_version: number;
  employees: number;
  total_incentive: number;
  net_payable: number;
  accumulation: number;
}

export interface TargetRow {
  employee_id: string;
  full_name?: string;
  period: string;
  target_units: number;
  winner_units: number;
  status: string;
  region?: string;
  version?: number;
}

export interface EmployeeRow {
  employee_id: string;
  full_name: string;
  email: string | null;
  role: string;
  designation: string | null;
  region: string | null;
  zone: string | null;
  submanager_id: string | null;
  rm_id: string | null;
  zm_id: string | null;
  is_active: boolean;
}

export interface RuleRow {
  rule_id: string;
  scope: string;
  threshold: number;
  rate: number;
  effective_from: string;
  source_note: string | null;
}

export interface AuditRow {
  occurred_at: string;
  user_email: string;
  action: string;
  affected_record: string | null;
  reason: string | null;
}
