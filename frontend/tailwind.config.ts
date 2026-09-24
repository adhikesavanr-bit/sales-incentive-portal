import type { Config } from "tailwindcss";

/**
 * Tokens for an internal incentive ledger.
 *
 * The palette is clinical rather than corporate: a deep teal carries structure,
 * a muted green and a burnt rust carry the only two verdicts that matter in
 * this product — qualified and disqualified — so colour is never decorative.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#16191F", muted: "#5B6472", faint: "#8C95A3" },
        canvas: "#F1F3F5",
        surface: "#FFFFFF",
        rule: "#DDE1E6",
        teal: { DEFAULT: "#0E4F52", deep: "#093437", wash: "#E4EEEE" },
        qualified: { DEFAULT: "#2F7D5E", wash: "#E6F1EC" },
        disqualified: { DEFAULT: "#B1543A", wash: "#F7EAE5" },
        deferred: { DEFAULT: "#7A6096", wash: "#EFEAF4" },
      },
      fontFamily: {
        // Installed fonts only, so nothing is downloaded before text renders.
        // Calibri where Office is installed (Carlito is its metric-compatible
        // open twin), Times New Roman everywhere else.
        sans: ["Calibri", "Carlito", '"Times New Roman"', "Times", "serif"],
      },
      fontSize: {
        // A type scale built on a 1.25 ratio, with a display size reserved
        // for the one number a BDE opens the app to see.
        micro: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }],
        display: ["2.75rem", { lineHeight: "1.05", letterSpacing: "-0.02em" }],
      },
      borderRadius: { card: "3px" },
      boxShadow: { card: "0 1px 2px rgba(22,25,31,0.06)" },
    },
  },
  plugins: [],
};
export default config;
