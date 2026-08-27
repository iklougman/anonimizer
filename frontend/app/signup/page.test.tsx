import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { createTenantSignup } from "@/lib/api/signup";

vi.mock("@/lib/api/signup", () => ({ createTenantSignup: vi.fn() }));

import SignupPage from "./page";

// Same rationale as app/login/page.test.tsx -- this page is wrapped in the
// app's real MantineProvider (see app/layout.tsx) at runtime, so it needs one
// in the render tree here too.
function renderWithMantine(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

async function fillForm() {
  await userEvent.type(screen.getByLabelText("Praxisname"), "Test Praxis");
  await userEvent.type(screen.getByLabelText("Vorname"), "Anna");
  await userEvent.type(screen.getByLabelText("Nachname"), "Schmitt");
  await userEvent.type(screen.getByLabelText("E-Mail"), "anna@example.test");
  await userEvent.type(screen.getByLabelText("Passwort"), "correct-horse-battery-staple");
}

describe("SignupPage", () => {
  it("submits the form and shows a check-your-email confirmation on success", async () => {
    (createTenantSignup as any).mockResolvedValue({ tenant_id: "t1", verification_email_sent: true });
    renderWithMantine(<SignupPage />);
    await fillForm();
    await userEvent.click(screen.getByRole("button", { name: "Registrieren" }));
    await waitFor(() => expect(screen.getByText(/E-Mail/i)).toBeInTheDocument());
    expect(createTenantSignup).toHaveBeenCalledWith({
      practice_name: "Test Praxis",
      first_name: "Anna",
      last_name: "Schmitt",
      email: "anna@example.test",
      password: "correct-horse-battery-staple",
    });
    expect(screen.getByRole("link", { name: "Zurück zum Login" })).toHaveAttribute("href", "/login");
  });

  it("shows an error message when signup fails", async () => {
    (createTenantSignup as any).mockRejectedValue(new Error("a Keycloak user with this email already exists"));
    renderWithMantine(<SignupPage />);
    await fillForm();
    await userEvent.click(screen.getByRole("button", { name: "Registrieren" }));
    await waitFor(() => expect(screen.getByText(/fehlgeschlagen|existiert/i)).toBeInTheDocument());
  });
});
