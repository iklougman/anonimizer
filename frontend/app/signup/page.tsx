"use client";

import { useState } from "react";
import { Button, Container, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import Link from "next/link";
import { createTenantSignup } from "@/lib/api/signup";

type Status = "idle" | "submitting" | "success" | "error";

export default function SignupPage() {
  const [practiceName, setPracticeName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState("");

  const submitting = status === "submitting";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setStatus("submitting");
    setErrorMessage("");
    try {
      await createTenantSignup({
        practice_name: practiceName,
        first_name: firstName,
        last_name: lastName,
        email,
        password,
      });
      setStatus("success");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Registrierung fehlgeschlagen.");
      setStatus("error");
    }
  }

  if (status === "success") {
    return (
      <Container size="xs" style={{ paddingTop: "15vh" }}>
        <Paper withBorder p="xl" radius="md">
          <Stack gap="lg">
            <Title order={2} ta="center">
              Praxis registriert
            </Title>
            <Text ta="center">
              Bitte prüfen Sie Ihr E-Mail-Postfach, um Ihre Registrierung zu bestätigen.
            </Text>
            <Text ta="center" size="sm">
              <Text component={Link} href="/login" c="blue" inherit>
                Zurück zum Login
              </Text>
            </Text>
          </Stack>
        </Paper>
      </Container>
    );
  }

  return (
    <Container size="xs" style={{ paddingTop: "15vh" }}>
      <Paper withBorder p="xl" radius="md">
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <Title order={2} ta="center">
              Praxis registrieren
            </Title>
            <TextInput
              label="Praxisname"
              value={practiceName}
              onChange={(event) => setPracticeName(event.currentTarget.value)}
              required
              withAsterisk={false}
            />
            <TextInput
              label="Vorname"
              value={firstName}
              onChange={(event) => setFirstName(event.currentTarget.value)}
              required
              withAsterisk={false}
            />
            <TextInput
              label="Nachname"
              value={lastName}
              onChange={(event) => setLastName(event.currentTarget.value)}
              required
              withAsterisk={false}
            />
            <TextInput
              label="E-Mail"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              required
              withAsterisk={false}
            />
            <PasswordInput
              label="Passwort"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              required
              withAsterisk={false}
            />
            {status === "error" && (
              <Text c="red" size="sm">
                Registrierung fehlgeschlagen: {errorMessage}
              </Text>
            )}
            <Button type="submit" size="md" disabled={submitting}>
              {submitting ? "Wird registriert…" : "Registrieren"}
            </Button>
            <Text ta="center" size="sm" c="dimmed">
              Bereits registriert?{" "}
              <Text component={Link} href="/login" c="blue" inherit>
                Anmelden
              </Text>
            </Text>
          </Stack>
        </form>
      </Paper>
    </Container>
  );
}
