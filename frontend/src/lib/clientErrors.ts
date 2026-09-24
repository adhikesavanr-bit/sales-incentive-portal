/**
 * Client-side crash handling.
 *
 * The common crash is not a bug in a page: after a deploy, a tab opened on the
 * previous build asks for JavaScript chunks that no longer exist, and the
 * navigation dies. Reloading picks up the new build, so that case is handled
 * silently. Anything else is reported to the API log so it can be diagnosed.
 */
import { getToken } from "@/lib/api";

const RELOAD_KEY = "incentive_portal_chunk_reload_at";

export function isChunkError(err: unknown): boolean {
  const e = err as { name?: string; message?: string } | null;
  const text = `${e?.name ?? ""} ${e?.message ?? String(err ?? "")}`;
  return /ChunkLoadError|Loading chunk \S+ failed|Loading CSS chunk|Failed to fetch dynamically imported module|error loading dynamically imported module/i.test(
    text,
  );
}

/**
 * Reload to pick up the new build — at most once every 30 seconds, so a chunk
 * that is genuinely missing cannot put the page into a reload loop.
 * Returns false when it declined to reload.
 */
export function reloadForNewBuild(): boolean {
  try {
    const last = Number(window.sessionStorage.getItem(RELOAD_KEY) ?? 0);
    if (Date.now() - last < 30_000) return false;
    window.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    /* storage unavailable: reload anyway, once, for this event */
  }
  window.location.reload();
  return true;
}

export function reportClientError(err: unknown, kind: string, digest?: string): void {
  const token = getToken();
  if (!token) return; // the endpoint is for signed-in users only
  const e = err as { message?: string; stack?: string } | null;
  try {
    void fetch("/api/client-errors", {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        kind,
        message: String(e?.message ?? err ?? "").slice(0, 1000),
        stack: String(e?.stack ?? "").slice(0, 4000),
        url: window.location.pathname.slice(0, 500),
        user_agent: navigator.userAgent.slice(0, 300),
        digest,
      }),
    }).catch(() => {});
  } catch {
    /* reporting must never throw */
  }
}

/** Shared by the error boundaries: recover from a stale build, else report. */
export function handleBoundaryError(err: Error & { digest?: string }): void {
  if (isChunkError(err) && reloadForNewBuild()) return;
  reportClientError(err, isChunkError(err) ? "chunk" : "boundary", err.digest);
}
