"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, clearToken, endViewAs, getToken, type Me } from "@/lib/api";

const NAV = [
  { href: "/dashboard", label: "My performance", needs: null },
  { href: "/team", label: "My team", needs: "VIEW_TEAM" },
  { href: "/admin/upload", label: "Sales upload", needs: "UPLOAD_SALES" },
  { href: "/admin/targets", label: "Targets", needs: "VIEW_TARGETS" },
  { href: "/admin/rules", label: "Rules", needs: "VIEW_TARGETS" },
  { href: "/admin/employees", label: "People", needs: "MANAGE_EMPLOYEES" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api.me().then(setMe).catch(() => router.replace("/login"));
  }, [router]);

  if (!me) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-ink-muted">
        Loading your account…
      </div>
    );
  }

  const visible = NAV.filter((n) => !n.needs || me.permissions.includes(n.needs));

  function signOut() {
    clearToken();
    router.replace("/login");
  }

  const viewingAs = !!me.impersonated_by;

  return (
    <>
    {viewingAs && (
      <div
        role="status"
        className="sticky top-0 z-40 flex flex-wrap items-center justify-center gap-x-3 gap-y-1
                   bg-amber-500 px-4 py-2 text-sm font-medium text-ink"
      >
        <span>
          Viewing as {me.full_name} ({me.role.replace(/_/g, " ").toLowerCase()}
          {me.region ? ` · ${me.region}` : ""}) — read-only
        </span>
        <button onClick={() => endViewAs()} className="rounded-card bg-ink px-3 py-1 text-white">
          Exit view-as
        </button>
      </div>
    )}
    <div className="min-h-screen lg:flex">
      <aside className="border-b border-rule bg-surface lg:h-screen lg:w-60 lg:shrink-0 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-5 py-4 lg:block">
          <div>
            <div className="text-sm font-semibold tracking-tight">Field Incentive</div>
            <div className="text-micro text-ink-faint">Marrow sales</div>
          </div>
        </div>

        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:mt-2 lg:flex-col lg:overflow-visible lg:px-3">
          {visible.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`whitespace-nowrap rounded-card px-3 py-2 text-sm ${
                  active
                    ? "bg-teal-wash font-medium text-teal-deep"
                    : "text-ink-muted hover:bg-canvas"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="hidden border-t border-rule px-5 py-4 lg:block lg:mt-auto">
          <div className="text-sm font-medium">{me.full_name}</div>
          <div className="text-micro text-ink-faint">
            {me.role.replace(/_/g, " ").toLowerCase()}
            {me.region ? ` · ${me.region}` : ""}
          </div>
          {viewingAs ? (
            <button onClick={() => endViewAs()} className="mt-3 text-micro text-ink-muted underline">
              Exit view-as
            </button>
          ) : (
            <button onClick={signOut} className="mt-3 text-micro text-ink-muted underline">
              Sign out
            </button>
          )}
        </div>
      </aside>

      <main className="flex-1 p-4 sm:p-6 lg:h-screen lg:overflow-y-auto lg:p-10">
        {children}
      </main>
    </div>
    </>
  );
}
