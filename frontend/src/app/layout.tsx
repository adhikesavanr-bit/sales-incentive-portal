import type { Metadata } from "next";
import { Public_Sans } from "next/font/google";

import { ClientErrorListener } from "@/components/ClientErrorListener";

import "./globals.css";

// One family, used across display and body. Public Sans has real tabular
// numerals, which a ledger needs more than it needs a second typeface.
const publicSans = Public_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-public-sans",
});

export const metadata: Metadata = {
  title: "Field Incentive — Marrow sales",
  description: "Targets, qualified sales and incentive for the field sales team.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={publicSans.variable}>
      <body>
        <ClientErrorListener />
        {children}
      </body>
    </html>
  );
}
