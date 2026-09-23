"use client";

import Script from "next/script";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { api, setToken } from "@/lib/api";

declare global {
  interface Window {
    google?: any;
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  const handleCredential = useCallback(
    async (response: { credential: string }) => {
      try {
        const { access_token } = await api.login(response.credential);
        setToken(access_token);
        router.replace("/dashboard");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Sign-in failed.");
      }
    },
    [router],
  );

  const initGoogle = useCallback(() => {
    window.google?.accounts.id.initialize({
      client_id: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
      callback: handleCredential,
    });
    window.google?.accounts.id.renderButton(
      document.getElementById("google-signin"),
      { theme: "outline", size: "large", width: 320, text: "signin_with" },
    );
  }, [handleCredential]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Script src="https://accounts.google.com/gsi/client" onLoad={initGoogle} />
      <div className="panel w-full max-w-md p-8">
        <div className="text-sm font-semibold tracking-tight">Field Incentive</div>
        <h1 className="mt-6 text-2xl font-semibold tracking-tight">
          Sign in to see your incentive
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Use your Marrow or DailyRounds account. Your targets, sales and payout
          are calculated from the approved incentive policy.
        </p>

        <div id="google-signin" className="mt-8" />

        {process.env.NEXT_PUBLIC_ALLOW_DEV_LOGIN === "true" && (
          <div className="mt-6 border-t border-rule pt-6">
            <button
              onClick={async () => {
                try {
                  const { access_token } = await api.devLogin();
                  setToken(access_token);
                  router.replace("/dashboard");
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Sign-in failed.");
                }
              }}
              className="btn-quiet w-full"
            >
              Sign in for local development
            </button>
            <p className="mt-2 text-micro text-ink-faint">
              Available only on this machine, and only while
              ALLOW_DEV_LOGIN is set on the API.
            </p>
          </div>
        )}

        {error && (
          <p
            role="alert"
            className="mt-6 rounded-card bg-disqualified-wash px-4 py-3 text-sm text-disqualified"
          >
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
