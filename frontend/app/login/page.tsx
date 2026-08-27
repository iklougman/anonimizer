"use client";

import { Button, Container, Paper, Stack, Text, Title } from "@mantine/core";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

export default function LoginPage() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/dashboard";

  return (
    <Container size="xs" style={{ paddingTop: "15vh" }}>
      <Paper withBorder p="xl" radius="md">
        <Stack gap="lg">
          <Title order={2} ta="center">
            Aigenta
          </Title>
          <Button size="md" onClick={() => signIn("keycloak", { callbackUrl })}>
            Anmelden
          </Button>
          <Text ta="center" size="sm" c="dimmed">
            Neu hier?{" "}
            <Text component={Link} href="/signup" c="blue" inherit>
              Praxis registrieren
            </Text>
          </Text>
        </Stack>
      </Paper>
    </Container>
  );
}
