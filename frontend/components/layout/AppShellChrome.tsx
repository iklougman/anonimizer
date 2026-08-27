"use client";

import {
  ActionIcon,
  AppShell,
  Avatar,
  Burger,
  Container,
  Divider,
  Group,
  Menu,
  Stack,
  Text,
  UnstyledButton,
  Button,
  rem,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconChevronDown,
  IconDashboard,
  IconLogout,
  IconMoon,
  IconSettings,
  IconSun,
  IconUser,
} from "@tabler/icons-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import type { ComponentType, ReactNode } from "react";
import { AigentaLogo } from "@/components/AigentaLogo";
import { useAvailableApps } from "@/components/AvailableAppsProvider";
import { useMe } from "@/components/MeProvider";
import { getAppCatalogEntry } from "@/lib/appCatalog";

interface NavItem {
  label: string;
  href: string;
  icon: ComponentType<{ size?: number }>;
}

export function AppShellChrome({ children }: { children: ReactNode }) {
  const [opened, { toggle }] = useDisclosure();
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const pathname = usePathname();
  const me = useMe();
  const { apps } = useAvailableApps();
  const hasAdminAccess = me?.permissions.some((p) => p.startsWith("admin:")) ?? false;

  const navigationItems: NavItem[] = [
    { label: "Dashboard", href: "/dashboard", icon: IconDashboard },
    ...apps.map((app) => ({
      label: app.name,
      href: `/apps/${app.key}`,
      icon: getAppCatalogEntry(app.key).icon,
    })),
    ...(hasAdminAccess ? [{ label: "Verwaltung", href: "/admin", icon: IconSettings }] : []),
  ];

  function renderNavLink(item: NavItem) {
    const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
    const Icon = item.icon;
    return (
      <Button
        key={item.href}
        component={Link}
        href={item.href}
        variant={isActive ? "light" : "subtle"}
        leftSection={<Icon size={18} />}
        justify="flex-start"
        fullWidth
        mb={4}
        styles={{
          root: { paddingLeft: rem(12), paddingRight: rem(12), height: rem(42) },
          section: { marginRight: rem(12) },
          label: { fontWeight: isActive ? 600 : 500 },
        }}
      >
        {item.label}
      </Button>
    );
  }

  const initials = (me?.email ?? "?").slice(0, 2).toUpperCase();

  return (
    <AppShell
      header={{ height: 64 }}
      navbar={{ width: 260, breakpoint: "sm", collapsed: { mobile: !opened } }}
      padding="0"
    >
      <AppShell.Header withBorder>
        <Container fluid h="100%" px={32}>
          <Group h="100%" justify="space-between">
            <Group gap="md">
              <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
              {/* Reference always uses variant="dark" (deep blue) regardless
                  of color scheme -- switched here by the current scheme
                  instead, since this app's dark mode actually changes the
                  header background, unlike the reference's. */}
              <AigentaLogo size="sm" variant={colorScheme === "dark" ? "light" : "dark"} />
              <Divider orientation="vertical" h={24} visibleFrom="sm" />
              <Text size="sm" fw={600} visibleFrom="sm">
                {me?.tenant_name ?? ""}
              </Text>
            </Group>
            <Group gap="sm">
              <ActionIcon
                variant="subtle"
                color="gray"
                size="lg"
                onClick={toggleColorScheme}
                aria-label="Farbschema wechseln"
              >
                {colorScheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
              </ActionIcon>
              <Menu shadow="md" width={220} position="bottom-end">
                <Menu.Target>
                  <UnstyledButton>
                    <Group gap="xs">
                      <Avatar size="sm" radius="xl" color="teal">
                        {initials}
                      </Avatar>
                      <Stack gap={0} visibleFrom="sm">
                        <Text size="sm" fw={500} lh={1.2}>
                          {me?.email ?? ""}
                        </Text>
                        <Text size="xs" c="dimmed" lh={1.2}>
                          {me?.role ?? ""}
                        </Text>
                      </Stack>
                      <IconChevronDown size={14} />
                    </Group>
                  </UnstyledButton>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item leftSection={<IconUser size={14} />} disabled>
                    Profil
                  </Menu.Item>
                  <Menu.Divider />
                  <Menu.Item leftSection={<IconLogout size={14} />} color="red" onClick={() => signOut({ callbackUrl: "/login" })}>
                    Abmelden
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </Group>
          </Group>
        </Container>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap={4}>
          <Text size="xs" fw={700} c="dimmed" mb="xs" pl={12}>
            HAUPTMENÜ
          </Text>
          {navigationItems.map(renderNavLink)}
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main bg="var(--color-bg-subtle)">
        <Container fluid style={{ paddingLeft: 32, paddingRight: 32, paddingTop: 24, paddingBottom: 40 }}>
          {children}
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}
