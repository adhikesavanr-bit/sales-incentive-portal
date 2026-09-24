import type { Metadata } from "next";

import { ClientErrorListener } from "@/components/ClientErrorListener";

import "./globals.css";

export const metadata: Metadata = {
  title: "Field Incentive — Marrow sales",
  description: "Targets, qualified sales and incentive for the field sales team.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ClientErrorListener />
        {children}
      </body>
    </html>
  );
}
