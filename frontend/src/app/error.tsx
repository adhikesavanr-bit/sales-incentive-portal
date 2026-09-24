"use client";

import { useEffect } from "react";

import { ErrorScreen } from "@/components/ErrorScreen";
import { handleBoundaryError } from "@/lib/clientErrors";

/** Catches a crash in any page, below the root layout. */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => handleBoundaryError(error), [error]);
  return <ErrorScreen error={error} reset={reset} />;
}
