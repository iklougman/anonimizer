"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Box, Tabs, Text } from "@mantine/core";
import { useMe } from "@/components/MeProvider";

const TABS = [
  { href: "/admin/users", label: "Benutzer", permission: "admin:users:manage" },
  { href: "/admin/branches", label: "Filialen", permission: "admin:branches:manage" },
  { href: "/admin/permissions", label: "Berechtigungen", permission: "admin:permissions:manage" },
  { href: "/admin/apps", label: "Apps", permission: "admin:apps:manage" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const me = useMe();
  const pathname = usePathname();

  // The backend enforces every admin permission independently per route --
  // this gate is UX only (avoid rendering a broken admin shell to someone
  // with no admin access at all), not the source of truth.
  const hasAnyAdminPermission = me?.permissions.some((p) => p.startsWith("admin:")) ?? false;

  if (me === null) {
    return <Text c="dimmed">Lädt…</Text>;
  }

  if (!hasAnyAdminPermission) {
    return <Text c="dimmed">Kein Zugriff auf die Verwaltung.</Text>;
  }

  const visibleTabs = TABS.filter((tab) => me.permissions.includes(tab.permission));

  return (
    <Box>
      <Tabs value={pathname} mb={24}>
        <Tabs.List>
          {visibleTabs.map((tab) => (
            <Tabs.Tab
              key={tab.href}
              value={tab.href}
              renderRoot={(rootProps) => (
                <Link href={tab.href} {...rootProps}>
                  {tab.label}
                </Link>
              )}
            />
          ))}
        </Tabs.List>
      </Tabs>
      {children}
    </Box>
  );
}
