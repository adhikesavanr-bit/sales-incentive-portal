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
  window.sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  window.sessionStorage.removeItem(VIEW_AS_KEY);
}

// --- view as ----------------------------------------------------------------
// While a super admin views the app as someone, the view-as token is the
// session token and the admin's own token waits here until they exit.
const ADMIN_TOKEN_KEY = "incentive_portal_admin_token";
const VIEW_AS_KEY = "incentive_portal_view_as";

export function isViewingAs(): boolean {
  return typeof window !== "undefined" && !!window.sessionStorage.getItem(ADMIN_TOKEN_KEY);
}

/** Back to the admin's own session. Returns false if there was none to restore. */
function restoreAdminSession(): boolean {
  const admin = window.sessionStorage.getItem(ADMIN_TOKEN_KEY);
  if (!admin) return false;
  window.sessionStorage.setItem(TOKEN_KEY, admin);
  window.sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  window.sessionStorage.removeItem(VIEW_AS_KEY);
  return true;
}

/**
 * A 401 normally means sign in again. During view-as it means the view-as
 * session ended (30 minutes, or access changed): return to the admin's own
 * session instead of signing them out.
 */
function onUnauthorized(): never {
  if (restoreAdminSession()) {
    window.location.href = "/admin/employees?view_as=ended";
    throw new ApiError(401, "View-as ended. You are back in your own account.");
  }
  clearToken();
  window.location.href = "/login";
  throw new ApiError(401, "Your session expired. Sign in again.");
}

export async function startViewAs(employeeId: string): Promise<void> {
  const res = await request<{ access_token: string; employee_id: string }>(
    "/api/auth/view-as",
    { method: "POST", body: JSON.stringify({ employee_id: employeeId }) },
  );
  const own = getToken();
  if (own) window.sessionStorage.setItem(ADMIN_TOKEN_KEY, own);
  window.sessionStorage.setItem(VIEW_AS_KEY, res.employee_id);
  setToken(res.access_token);
  window.location.href = "/dashboard";
}

export async function endViewAs(): Promise<void> {
  const viewed = window.sessionStorage.getItem(VIEW_AS_KEY);
  if (!restoreAdminSession()) return;
  // Best effort: the audit entry for the end. Exiting never waits on it.
  if (viewed) {
    request("/api/auth/view-as/end", {
      method: "POST",
      body: JSON.stringify({ employee_id: viewed }),
    }).catch(() => {});
  }
  window.location.href = "/admin/employees";
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
    if (typeof window !== "undefined") onUnauthorized();
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

/**
 * Download a file from the API. A plain <a href> cannot be used: the browser
 * navigates without the Authorization header, and the API answers 401. So the
 * file is fetched with the token and handed to the browser as a blob.
 */
async function download(path: string, fallbackName: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) onUnauthorized();
  if (!res.ok) {
    let detail = `Download failed (${res.status}).`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const name =
    /filename="?([^";]+)"?/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ??
    fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Every page mounts its own AppShell, which needs the signed-in account. It is
// fetched once per session token rather than on every navigation; signing
// out, or starting or ending view-as, changes the token and so refetches.
type MeCache = { token: string; value: Promise<Me>; resolved?: Me };
let meCache: MeCache | null = null;

/** The account already loaded for the current token, if any. */
export function cachedMe(): Me | null {
  const entry = meCache;
  return entry && entry.token === (getToken() ?? "") ? entry.resolved ?? null : null;
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

  me: (): Promise<Me> => {
    const token = getToken() ?? "";
    if (meCache?.token !== token) {
      const value = request<Me>("/api/auth/me");
      const entry: MeCache = { token, value };
      meCache = entry;
      value.then(
        (me) => { entry.resolved = me; },
        () => { if (meCache === entry) meCache = null; },
      );
    }
    return meCache!.value;
  },

  myDashboard: (period: string) =>
    request<Breakdown>(`/api/me/dashboard?period=${period}`),

  // 1000 is the API's ceiling; a month's sales for one person fit well within it.
  mySales: (period: string, limit = 1000, offset = 0) =>
    request<Transaction[]>(
      `/api/me/sales?period=${period}&limit=${limit}&offset=${offset}`,
    ),

  employeeDashboard: (id: string, period: string) =>
    request<Breakdown>(`/api/employees/${id}/dashboard?period=${period}`),

  employeeSales: (id: string, period: string) =>
    request<Transaction[]>(`/api/employees/${id}/sales?period=${period}&limit=1000`),

  myCoupons: (period: string) =>
    request<CouponVerdict[]>(`/api/me/coupons?period=${period}`),

  employeeCoupons: (id: string, period: string) =>
    request<CouponVerdict[]>(`/api/employees/${id}/coupons?period=${period}`),

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


  rules: () => request<RuleRow[]>("/api/incentive/rules"),

  employees: (includeInactive = false) =>
    request<EmployeeRow[]>(`/api/employees?include_inactive=${includeInactive}`),

  assignableRoles: () => request<RoleOption[]>("/api/employees/roles"),

  createEmployee: (body: EmployeeRow, reason: string) =>
    request<EmployeeRow>("/api/employees", {
      method: "POST",
      body: JSON.stringify({ ...body, reason }),
    }),

  updateEmployee: (id: string, body: EmployeeRow, reason: string) =>
    request<EmployeeRow>(`/api/employees/${id}`, {
      method: "PUT",
      body: JSON.stringify({ ...body, reason }),
    }),

  deactivateEmployee: (id: string, exitDate: string, reason: string) =>
    request<EmployeeRow>(`/api/employees/${id}/deactivate`, {
      method: "POST",
      body: JSON.stringify({ exit_date: exitDate, reason }),
    }),

  couponRules: (period: string) =>
    request<{ period: string; rules: CouponRule[]; editable: boolean }>(
      `/api/rules/coupons?period=${period}`,
    ),

  updateCouponRule: (body: {
    group_size: string;
    required_sales: number;
    min_sales: number;
    min_own_sales: number;
    effective_from: string;
    reason: string;
  }) =>
    request<CouponRule>("/api/rules/coupons", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  slabs: (period: string) =>
    request<{ period: string; scopes: Record<string, SlabRow[]>; editable: boolean }>(
      `/api/rules/slabs?period=${period}`,
    ),

  updateSlab: (body: {
    scope: string;
    threshold: number;
    rate: number;
    effective_from: string;
    reason: string;
  }) =>
    request<SlabRow>("/api/rules/slabs", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  audit: (limit = 200) => request<AuditRow[]>(`/api/audit?limit=${limit}`),

  exportCsv: (report: string, period: string) =>
    download(`/api/export/${report}?period=${period}`, `${report}-${period}.csv`),

  /** One person's statement. Omit employeeId for the caller's own. */
  exportStatementPdf: (period: string, employeeId?: string) =>
    download(
      `/api/export/statement?period=${period}` +
        (employeeId ? `&employee_id=${encodeURIComponent(employeeId)}` : ""),
      `incentive-${employeeId ?? "me"}-${period}.pdf`,
    ),

  downloadUploadErrors: (batchId: string) =>
    download(`/api/sales/validate/${batchId}/errors.csv`, `${batchId}-errors.csv`),
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
  /** Set when a super admin is viewing the app as this person. */
  impersonated_by?: string | null;
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

/** One coupon's verdict for a month: the workbook's "Coupon Analysis" block. */
export interface CouponVerdict {
  coupon_signature: string;
  coupon_code: string;
  college_id: string | null;
  group_size: string | null;
  required_sales: number | null;
  activation_date: string | null;
  total: number;
  club_sales: number | null;
  min_sales: number | null;
  min_own_sales: number | null;
  is_foundation: boolean | null;
  qualified: boolean;
  overridden: boolean | null;
  override_reason: string | null;
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
  // The list is everyone in scope, LEFT JOINed to targets: someone with no
  // target for the period comes back with these four as null.
  period: string | null;
  target_units: number | null;
  winner_units: number | null;
  status: string | null;
  region?: string;
  version?: number;
}

export interface EmployeeRow {
  employee_id: string;
  full_name: string;
  email?: string | null;
  initial?: string | null;
  role: string;
  designation?: string | null;
  region?: string | null;
  zone?: string | null;
  submanager_id?: string | null;
  rm_id?: string | null;
  zm_id?: string | null;
  is_active: boolean;
  exit_date?: string | null;
}

export interface RoleOption {
  value: string;
  label: string;
}

export interface CouponRule {
  group_size: string;
  required_sales: number;
  min_sales: number;
  min_own_sales: number;
  effective_from: string | null;
  effective_to: string | null;
  reason: string | null;
  updated_by: string | null;
}

export interface SlabRow {
  scope: string;
  threshold: number;
  rate: number;
  effective_from: string | null;
  source_note: string | null;
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
