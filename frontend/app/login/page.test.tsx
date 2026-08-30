import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { signIn } from "next-auth/react";

vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("callbackUrl=%2Fdashboard"),
}));

import LoginPage from "./page";

// Same rationale as app/(shell)/admin/layout.test.tsx -- this page is wrapped
// in the app's real MantineProvider (see app/layout.tsx) at runtime, so it
// needs one in the render tree here too.
function renderWithMantine(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

describe("LoginPage", () => {
  it("calls signIn with keycloak and the callback URL on Anmelden", async () => {
    renderWithMantine(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(signIn).toHaveBeenCalledWith("keycloak", { callbackUrl: "/dashboard" });
  });

  it("links to /signup", () => {
    renderWithMantine(<LoginPage />);
    expect(screen.getByRole("link", { name: "Praxis registrieren" })).toHaveAttribute("href", "/signup");
  });
});
