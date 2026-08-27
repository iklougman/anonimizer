"use client";

import { useEffect } from "react";
import { signOut, useSession } from "next-auth/react";

/** Centralized reaction to a broken session, mounted once at the root
 * (see SessionProviderWrapper) so every route is covered -- previously
 * only ConversationSidebar.tsx reacted to this, leaving every other
 * page free to keep firing API calls with a stale/expired accessToken. */
export function SessionErrorHandler() {
  const { data: session } = useSession();

  useEffect(() => {
    if (session?.error === "RefreshAccessTokenError") {
      signOut({ callbackUrl: "/login" });
    }
  }, [session?.error]);

  return null;
}
