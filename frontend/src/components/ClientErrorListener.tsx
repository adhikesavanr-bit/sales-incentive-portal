"use client";

import { useEffect } from "react";

import { isChunkError, reloadForNewBuild, reportClientError } from "@/lib/clientErrors";

/**
 * Catches what the error boundaries cannot: failures outside React rendering,
 * notably a chunk that fails to load during client-side navigation.
 */
export function ClientErrorListener() {
  useEffect(() => {
    function handle(err: unknown) {
      if (isChunkError(err)) {
        if (!reloadForNewBuild()) reportClientError(err, "chunk");
        return;
      }
      reportClientError(err, "error");
    }
    const onError = (e: ErrorEvent) => handle(e.error ?? e.message);
    const onRejection = (e: PromiseRejectionEvent) => handle(e.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}
