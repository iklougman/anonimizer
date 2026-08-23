"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { listApps, putAppAssignment } from "@/lib/api/admin";
import type { App } from "@/lib/api/types";
import styles from "./page.module.css";

function tenantWideAssignment(app: App) {
  return app.assignments.find((a) => a.branch_id === null) ?? null;
}

export default function AdminAppsPage() {
  const { data: session } = useSession();
  const [apps, setApps] = useState<App[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  function load() {
    if (!session?.accessToken) return;
    setLoadError(null);
    listApps(session.accessToken)
      .then(setApps)
      .catch(() => setLoadError("Apps konnten nicht geladen werden."));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [session?.accessToken]);

  async function handleToggle(app: App) {
    if (!session?.accessToken || !app.is_entitled) return;
    const current = tenantWideAssignment(app)?.is_enabled ?? false;
    setRowError((prev) => ({ ...prev, [app.id]: "" }));
    try {
      const updated = await putAppAssignment(session.accessToken, app.id, {
        branch_id: null,
        is_enabled: !current,
      });
      setApps((prev) =>
        prev.map((a) =>
          a.id === app.id
            ? { ...a, assignments: [updated, ...a.assignments.filter((x) => x.branch_id !== null)] }
            : a
        )
      );
    } catch (error) {
      setRowError((prev) => ({
        ...prev,
        [app.id]: error instanceof Error ? error.message : "Aktualisieren fehlgeschlagen.",
      }));
    }
  }

  return (
    <div>
      <h1 className={styles.heading}>Apps</h1>
      {loadError && <p className={styles.error}>{loadError}</p>}

      <table className={styles.table}>
        <thead>
          <tr>
            <th>App</th>
            <th>Abonniert</th>
            <th>Aktiv (praxisweit)</th>
          </tr>
        </thead>
        <tbody>
          {apps.map((app) => {
            const enabled = tenantWideAssignment(app)?.is_enabled ?? false;
            return (
              <tr key={app.id}>
                <td>
                  <div className={styles.appName}>{app.name}</div>
                  {app.description && <div className={styles.appDescription}>{app.description}</div>}
                </td>
                <td>
                  <span className={app.is_entitled ? styles.badgeEntitled : styles.badgeNotEntitled}>
                    {app.is_entitled ? "Abonniert" : "Nicht abonniert"}
                  </span>
                </td>
                <td>
                  <label
                    className={styles.toggleLabel}
                    title={app.is_entitled ? undefined : "Erst über den Betreiber abonnieren."}
                  >
                    <input
                      type="checkbox"
                      checked={enabled}
                      disabled={!app.is_entitled}
                      onChange={() => handleToggle(app)}
                    />
                  </label>
                  {rowError[app.id] && <div className={styles.rowError}>{rowError[app.id]}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
