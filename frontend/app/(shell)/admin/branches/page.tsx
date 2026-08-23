"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { AdminApiError, createBranch, deleteBranch, listBranches, updateBranch } from "@/lib/api/admin";
import type { Branch } from "@/lib/api/types";
import styles from "./page.module.css";

export default function AdminBranchesPage() {
  const { data: session } = useSession();
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Record<string, string>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});

  function load() {
    if (!session?.accessToken) return;
    setLoadError(null);
    listBranches(session.accessToken)
      .then(setBranches)
      .catch(() => setLoadError("Filialen konnten nicht geladen werden."));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [session?.accessToken]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!session?.accessToken || !newName.trim()) return;
    setCreateError(null);
    try {
      const created = await createBranch(session.accessToken, newName.trim());
      setBranches((current) => [...current, created]);
      setNewName("");
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Anlegen fehlgeschlagen.");
    }
  }

  async function handleRename(branch: Branch) {
    if (!session?.accessToken) return;
    const name = renaming[branch.id]?.trim();
    if (!name || name === branch.name) return;
    setRowError((current) => ({ ...current, [branch.id]: "" }));
    try {
      const updated = await updateBranch(session.accessToken, branch.id, name);
      setBranches((current) => current.map((b) => (b.id === branch.id ? updated : b)));
    } catch (error) {
      setRowError((current) => ({
        ...current,
        [branch.id]: error instanceof Error ? error.message : "Umbenennen fehlgeschlagen.",
      }));
    }
  }

  async function handleDelete(branch: Branch) {
    if (!session?.accessToken) return;
    setRowError((current) => ({ ...current, [branch.id]: "" }));
    try {
      await deleteBranch(session.accessToken, branch.id);
      setBranches((current) => current.filter((b) => b.id !== branch.id));
    } catch (error) {
      setRowError((current) => ({
        ...current,
        [branch.id]:
          error instanceof AdminApiError && error.status === 409
            ? "Filiale hat noch zugewiesene Benutzer — erst Benutzer umziehen."
            : "Löschen fehlgeschlagen.",
      }));
    }
  }

  return (
    <div>
      <h1 className={styles.heading}>Filialen</h1>
      {loadError && <p className={styles.error}>{loadError}</p>}

      <table className={styles.table}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Benutzer</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {branches.map((branch) => (
            <tr key={branch.id}>
              <td>
                <input
                  className={styles.renameInput}
                  value={renaming[branch.id] ?? branch.name}
                  onChange={(e) =>
                    setRenaming((current) => ({ ...current, [branch.id]: e.target.value }))
                  }
                  onBlur={() => handleRename(branch)}
                />
              </td>
              <td>{branch.user_count}</td>
              <td>
                <button type="button" className={styles.deleteButton} onClick={() => handleDelete(branch)}>
                  Löschen
                </button>
                {rowError[branch.id] && <div className={styles.rowError}>{rowError[branch.id]}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className={styles.subheading}>Filiale anlegen</h2>
      <form className={styles.form} onSubmit={handleCreate}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Name der Filiale"
          required
        />
        <button type="submit" className={styles.submitButton}>
          Anlegen
        </button>
      </form>
      {createError && <p className={styles.error}>{createError}</p>}
    </div>
  );
}
