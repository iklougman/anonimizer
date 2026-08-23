"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useSession } from "next-auth/react";
import { listAvailableApps } from "@/lib/api/apps";
import type { AvailableApp } from "@/lib/api/types";

interface AvailableAppsState {
  apps: AvailableApp[];
  loading: boolean;
  error: boolean;
}

const initialState: AvailableAppsState = { apps: [], loading: true, error: false };

const AvailableAppsContext = createContext<AvailableAppsState>(initialState);

/** Fetched once here (not per-consumer) so the dashboard grid and the global
 * sidebar nav -- both of which need "which apps can this user use" -- share
 * one request instead of duplicating it. */
export function AvailableAppsProvider({ children }: { children: ReactNode }) {
  const { data: session } = useSession();
  const [state, setState] = useState<AvailableAppsState>(initialState);

  useEffect(() => {
    if (!session?.accessToken) return;
    listAvailableApps(session.accessToken)
      .then((apps) => setState({ apps, loading: false, error: false }))
      // Fails closed to an empty list -- same convention as MeProvider.
      .catch(() => setState({ apps: [], loading: false, error: true }));
  }, [session?.accessToken]);

  return <AvailableAppsContext.Provider value={state}>{children}</AvailableAppsContext.Provider>;
}

export function useAvailableApps(): AvailableAppsState {
  return useContext(AvailableAppsContext);
}
