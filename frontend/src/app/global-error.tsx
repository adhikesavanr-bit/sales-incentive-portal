"use client";

import { useEffect } from "react";

import { ErrorScreen } from "@/components/ErrorScreen";
import { handleBoundaryError } from "@/lib/clientErrors";

import "./globals.css";

/** Last resort: a crash in the root layout itself. Must render its own <html>. */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => handleBoundaryError(error), [error]);
  return (
    <html lang="en">
      <body>
        <ErrorScreen error={error} reset={reset} />
      </body>
    </html>
  );
}
