"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { getPermissionMatrix, putPermissionMatrix } from "@/lib/api/admin";
import type { PermissionMatrix } from "@/lib/api/types";
import styles from "./page.module.css";

const ROLE_LABELS: Record<string, string> = {
  doctor: "Arzt",
  staff: "Personal",
};

const PERMISSION_LABELS: Record<string, string> = {
  "conversations:read:branch": "Sichtbarkeit Filiale",
  "conversations:read:all": "Sichtbarkeit Praxis",
};

export default function AdminPermissionsPage() {
  const { data: session } = useSession();
  const [data, setData] = useState<PermissionMatrix | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function load() {
    if (!session?.accessToken) return;
    setLoadError(null);
    getPermissionMatrix(session.accessToken)
      .then(setData)
      .catch(() => setLoadError("Berechtigungen konnten nicht geladen werden."));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [session?.accessToken]);

  function toggle(role: string, permission: string) {
    setData((current) => {
      if (!current) return current;
      return {
        ...current,
        matrix: {
          ...current.matrix,
          [role]: {
            ...current.matrix[role],
            [permission]: !current.matrix[role][permission],
          },
        },
      };
    });
  }

  async function handleSave() {
    if (!session?.accessToken || !data) return;
    setSaving(true);
    setSaveError(null);
    setSaveStatus(null);
    try {
      const updated = await putPermissionMatrix(session.accessToken, data.matrix);
      setData(updated);
      setSaveStatus("Gespeichert.");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Speichern fehlgeschlagen.");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <p className={styles.error}>{loadError}</p>;
  if (!data) return <p>Lädt…</p>;

  return (
    <div>
      <h1 className={styles.heading}>Berechtigungen</h1>
      <p className={styles.hint}>
        Legt fest, welche Unterhaltungen Ärzte und Personal zusätzlich zu ihren eigenen sehen
        können. Der Praxisinhaber (super_admin) sieht immer alles.
      </p>

      <table className={styles.table}>
        <thead>
          <tr>
            <th></th>
            {data.roles.map((role) => (
              <th key={role}>{ROLE_LABELS[role] ?? role}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.permissions.map((permission) => (
            <tr key={permission}>
              <td className={styles.permissionLabel}>{PERMISSION_LABELS[permission] ?? permission}</td>
              {data.roles.map((role) => (
                <td key={role}>
                  <span className={styles.checkboxCell}>
                    <input
                      type="checkbox"
                      checked={data.matrix[role]?.[permission] ?? false}
                      onChange={() => toggle(role, permission)}
                    />
                    {data.defaults[role]?.includes(permission) && (
                      <span className={styles.defaultBadge}>Standard</span>
                    )}
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      {saveError && <p className={styles.error}>{saveError}</p>}
      {saveStatus && <p className={styles.success}>{saveStatus}</p>}
      <button type="button" className={styles.saveButton} onClick={handleSave} disabled={saving}>
        {saving ? "Speichert…" : "Speichern"}
      </button>
    </div>
  );
}
