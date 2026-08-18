"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useSession } from "next-auth/react";
import { getMe } from "@/lib/api/me";
import type { Me } from "@/lib/api/types";

const MeContext = createContext<Me | null>(null);

export function MeProvider({ children }: { children: ReactNode }) {
  const { data: session } = useSession();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!session?.accessToken) return;
    getMe(session.accessToken)
      .then(setMe)
      // Fails closed: on error `me` stays null, so every permission check
      // (which treats null as "no permissions") denies rather than grants.
      .catch(() => setMe(null));
  }, [session?.accessToken]);

  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}

/** Null while loading, on error, or if used outside MeProvider -- callers
 * must treat null as "no permissions known yet", never as "admin". */
export function useMe(): Me | null {
  return useContext(MeContext);
}
