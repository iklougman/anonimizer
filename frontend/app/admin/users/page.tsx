"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import {
  AdminApiError,
  createUser,
  listBranches,
  listUsers,
  updateUser,
} from "@/lib/api/admin";
import type { AdminUser, Branch, Role } from "@/lib/api/types";
import styles from "./page.module.css";

const ROLES: Role[] = ["super_admin", "doctor", "staff"];

export default function AdminUsersPage() {
  const { data: session } = useSession();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [role, setRole] = useState<Role>("doctor");
  const [branchId, setBranchId] = useState("");
  const [keycloakSubject, setKeycloakSubject] = useState("");
  const [needsLinkMode, setNeedsLinkMode] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createStatus, setCreateStatus] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  function load() {
    if (!session?.accessToken) return;
    setLoadError(null);
    Promise.all([listUsers(session.accessToken), listBranches(session.accessToken)])
      .then(([userList, branchList]) => {
        setUsers(userList);
        setBranches(branchList);
      })
      .catch(() => setLoadError("Benutzer konnten nicht geladen werden."));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [session?.accessToken]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!session?.accessToken) return;
    setCreating(true);
    setCreateError(null);
    setCreateStatus(null);
    try {
      const created = await createUser(session.accessToken, {
        email,
        role,
        branch_id: branchId || null,
        first_name: firstName || undefined,
        last_name: lastName || undefined,
        keycloak_subject: needsLinkMode ? keycloakSubject : undefined,
      });
      setUsers((current) => [...current, created]);
      setCreateStatus(
        needsLinkMode
          ? "Benutzer verknüpft."
          : created.invite_email_sent
            ? "Benutzer angelegt, Einladung versendet."
            : "Benutzer angelegt (Einladung konnte nicht versendet werden)."
      );
      setEmail("");
      setFirstName("");
      setLastName("");
      setKeycloakSubject("");
      setNeedsLinkMode(false);
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 501) {
        setNeedsLinkMode(true);
        setCreateError(
          "Keycloak-Provisionierung ist nicht konfiguriert. Bitte den Benutzer in der Keycloak-Konsole anlegen und die Subject-ID unten eintragen."
        );
      } else {
        setCreateError(error instanceof Error ? error.message : "Anlegen fehlgeschlagen.");
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleRoleChange(user: AdminUser, newRole: Role) {
    if (!session?.accessToken) return;
    try {
      const updated = await updateUser(session.accessToken, user.id, { role: newRole });
      setUsers((current) => current.map((u) => (u.id === user.id ? updated : u)));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Aktualisierung fehlgeschlagen.");
    }
  }

  async function handleBranchChange(user: AdminUser, newBranchId: string) {
    if (!session?.accessToken) return;
    try {
      const updated = await updateUser(session.accessToken, user.id, {
        branch_id: newBranchId || null,
      });
      setUsers((current) => current.map((u) => (u.id === user.id ? updated : u)));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Aktualisierung fehlgeschlagen.");
    }
  }

  async function handleActiveToggle(user: AdminUser) {
    if (!session?.accessToken) return;
    try {
      const updated = await updateUser(session.accessToken, user.id, {
        is_active: !user.is_active,
      });
      setUsers((current) => current.map((u) => (u.id === user.id ? updated : u)));
    } catch (error) {
      setLoadError(
        error instanceof AdminApiError
          ? error.message
          : "Aktualisierung fehlgeschlagen."
      );
    }
  }

  return (
    <div>
      <h1 className={styles.heading}>Benutzer</h1>

      {loadError && <p className={styles.error}>{loadError}</p>}

      <table className={styles.table}>
        <thead>
          <tr>
            <th>E-Mail</th>
            <th>Rolle</th>
            <th>Filiale</th>
            <th>Aktiv</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>{user.email}</td>
              <td>
                <select value={user.role} onChange={(e) => handleRoleChange(user, e.target.value as Role)}>
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select
                  value={user.branch_id ?? ""}
                  onChange={(e) => handleBranchChange(user, e.target.value)}
                >
                  <option value="">—</option>
                  {branches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button
                  type="button"
                  className={user.is_active ? styles.activeToggleOn : styles.activeToggleOff}
                  onClick={() => handleActiveToggle(user)}
                >
                  {user.is_active ? "Aktiv" : "Inaktiv"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className={styles.subheading}>Benutzer anlegen</h2>
      <form className={styles.form} onSubmit={handleCreate}>
        <div className={styles.formRow}>
          <label>
            E-Mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            Vorname
            <input value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </label>
          <label>
            Nachname
            <input value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </label>
        </div>
        <div className={styles.formRow}>
          <label>
            Rolle
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label>
            Filiale
            <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
              <option value="">—</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {needsLinkMode && (
          <label className={styles.linkModeField}>
            Keycloak Subject-ID (aus der Keycloak-Konsole)
            <input
              value={keycloakSubject}
              onChange={(e) => setKeycloakSubject(e.target.value)}
              required
            />
          </label>
        )}
        {createError && <p className={styles.error}>{createError}</p>}
        {createStatus && <p className={styles.success}>{createStatus}</p>}
        <button type="submit" className={styles.submitButton} disabled={creating}>
          {creating ? "Wird angelegt…" : "Benutzer anlegen"}
        </button>
      </form>
    </div>
  );
}
