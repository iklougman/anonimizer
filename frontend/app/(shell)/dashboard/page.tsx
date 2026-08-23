"use client";

import { Badge, Box, Card, Group, SimpleGrid, Text, ThemeIcon, Title } from "@mantine/core";
import { IconApps } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useAvailableApps } from "@/components/AvailableAppsProvider";
import { useMe } from "@/components/MeProvider";
import { getAppCatalogEntry } from "@/lib/appCatalog";

export default function DashboardPage() {
  const router = useRouter();
  const me = useMe();
  const { apps, loading, error } = useAvailableApps();

  return (
    <Box>
      <Title order={1} size="2rem" fw={600} mb={8}>
        Dashboard
      </Title>
      <Text c="dimmed" size="sm" mb={32}>
        Verfügbare Anwendungen für {me?.tenant_name ?? "Ihre Praxis"}
      </Text>

      {loading && <Text c="dimmed">Lädt…</Text>}
      {error && <Text c="red">Anwendungen konnten nicht geladen werden.</Text>}

      {!loading && !error && apps.length === 0 && (
        <Box style={{ textAlign: "center", padding: "64px 0" }}>
          <ThemeIcon size={80} radius="xl" variant="light" color="gray" mx="auto">
            <IconApps size={40} />
          </ThemeIcon>
          <Text c="dimmed" mt={16}>
            Für Ihre Praxis sind aktuell keine Anwendungen freigeschaltet.
          </Text>
        </Box>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing={24} verticalSpacing={24}>
        {apps.map((app) => {
          const { icon: Icon, color } = getAppCatalogEntry(app.key);
          return (
            <Card
              key={app.key}
              padding={24}
              radius={8}
              withBorder
              onClick={() => router.push(`/apps/${app.key}`)}
              style={{
                cursor: "pointer",
                height: "100%",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <div>
                <Group justify="space-between" align="flex-start" mb="xs" wrap="nowrap">
                  <Group gap="sm" wrap="nowrap">
                    <ThemeIcon size={40} radius="md" variant="light" color={color}>
                      <Icon size={20} />
                    </ThemeIcon>
                    <div>
                      <Text fw={600} size="md" truncate title={app.name}>
                        {app.name}
                      </Text>
                      <Group gap={6} mt={4}>
                        <Badge size="xs" variant="dot" color="green">
                          Aktiv
                        </Badge>
                      </Group>
                    </div>
                  </Group>
                </Group>
                <Text size="sm" c="dimmed" lineClamp={2} mt={8} mb={24} style={{ minHeight: "2.8em" }}>
                  {app.description ?? ""}
                </Text>
              </div>
            </Card>
          );
        })}
      </SimpleGrid>
    </Box>
  );
}
