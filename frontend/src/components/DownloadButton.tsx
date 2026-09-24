"use client";

import { useState } from "react";

/**
 * A button that runs an authenticated download and says when it fails,
 * instead of a link that silently lands on a JSON error page.
 */
export function DownloadButton({
  onDownload,
  children,
  className = "btn-quiet",
}: {
  onDownload: () => Promise<void>;
  children: React.ReactNode;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await onDownload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-end">
      <button type="button" onClick={run} disabled={busy} className={className}>
        {busy ? "Preparing…" : children}
      </button>
      {error && (
        <span role="alert" className="mt-1 text-micro text-disqualified">
          {error}
        </span>
      )}
    </span>
  );
}
