"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMe } from "@/components/MeProvider";
import styles from "./layout.module.css";

const TABS = [
  { href: "/admin/users", label: "Benutzer", permission: "admin:users:manage" },
  { href: "/admin/branches", label: "Filialen", permission: "admin:branches:manage" },
  { href: "/admin/permissions", label: "Berechtigungen", permission: "admin:permissions:manage" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const me = useMe();
  const pathname = usePathname();

  // The backend enforces every admin permission independently per route --
  // this gate is UX only (avoid rendering a broken admin shell to someone
  // with no admin access at all), not the source of truth.
  const hasAnyAdminPermission = me?.permissions.some((p) => p.startsWith("admin:")) ?? false;

  if (me === null) {
    return <div className={styles.status}>Lädt…</div>;
  }

  if (!hasAnyAdminPermission) {
    return (
      <div className={styles.status}>
        <p>Kein Zugriff auf die Verwaltung.</p>
        <Link href="/" className={styles.backLink}>
          Zurück zum Chat
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Link href="/" className={styles.backLink}>
          ← Chat
        </Link>
        <nav className={styles.tabs}>
          {TABS.filter((tab) => me.permissions.includes(tab.permission)).map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              className={pathname === tab.href ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            >
              {tab.label}
            </Link>
          ))}
        </nav>
      </header>
      <main className={styles.main}>{children}</main>
    </div>
  );
}
