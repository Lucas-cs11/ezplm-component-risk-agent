import type { Metadata } from "next";
import "./globals.css";
import { AuthGuard } from "@/components/AuthGuard";

export const metadata: Metadata = {
  title: "eZmanbo — 智能元器件选型",
  description: "面向 eZ-PLM 的电子元器件智能选型与风险评估系统",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="h-screen overflow-hidden">
        <AuthGuard>{children}</AuthGuard>
      </body>
    </html>
  );
}
