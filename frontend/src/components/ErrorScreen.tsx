"use client";

/** What people see instead of Next.js's bare "Application error" page. */
export function ErrorScreen({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="panel max-w-md p-6">
        <h1 className="text-lg font-semibold">This page hit a problem</h1>
        <p className="mt-2 text-sm text-ink-muted">
          It has been reported automatically. Reloading usually fixes it; if it
          keeps happening, send Finance the details below.
        </p>
        <pre className="mt-4 overflow-x-auto whitespace-pre-wrap rounded-card bg-canvas p-3 text-micro text-ink-muted">
          {error.message || "Unknown error"}
          {error.digest ? `\nRef: ${error.digest}` : ""}
        </pre>
        <div className="mt-4 flex gap-2">
          <button onClick={() => window.location.reload()} className="btn-quiet">
            Reload page
          </button>
          {reset && (
            <button onClick={reset} className="text-sm text-ink-muted underline">
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
