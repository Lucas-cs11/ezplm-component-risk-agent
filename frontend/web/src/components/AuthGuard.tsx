"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/store/authStore";

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore();
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const authed = isAuthenticated();
    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

    if (isPublic) {
      if (authed) {
        router.replace("/");
      } else {
        setReady(true);
      }
      return;
    }

    if (!authed) {
      fetch("/admin/setup-required")
        .then((r) => r.json())
        .then((data) => {
          router.replace(data.setup_required ? "/register" : "/login");
        })
        .catch(() => router.replace("/login"));
      return;
    }

    setReady(true);
  }, [isAuthenticated, pathname, router]);

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-sm text-gray-400">加载中…</div>
      </div>
    );
  }

  return <>{children}</>;
}
