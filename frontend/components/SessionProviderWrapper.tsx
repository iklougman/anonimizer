"use client";

import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

export function SessionProviderWrapper({ children }: { children: ReactNode }) {
  // Without this, the client only re-fetches /api/auth/session (and so only
  // gives lib/auth.ts's jwt() callback a chance to refresh a near-expiry
  // access token) on window focus or a full page reload. Keycloak's default
  // access token lifespan is ~5 minutes, so a user who stays on one tab
  // typing a message hits a stale token and every API call 401s with no
  // recovery. Polling well under that lifespan keeps the token refreshed
  // before it goes stale.
  return <SessionProvider refetchInterval={60}>{children}</SessionProvider>;
}
