import type { Metadata } from "next";

import { BootstrapStrip } from "@/components/BootstrapStrip";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Pixii Market Intel",
    template: "%s · Pixii Market Intel",
  },
  description: "Amazon Best Sellers and competitive SKU intelligence, listing telemetry, review synthesis, and INR revenue estimates.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-zinc-50/40 text-zinc-900 antialiased">
        <BootstrapStrip />
        {children}
      </body>
    </html>
  );
}
