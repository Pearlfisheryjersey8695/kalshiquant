import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KalshiQuant — Trading Terminal",
  description: "Real-time prediction market intelligence dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
