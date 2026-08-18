import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

let mockSession: { accessToken?: string } | null = { accessToken: "token-123" };
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: mockSession }),
}));

import * as meApi from "@/lib/api/me";
import { MeProvider, useMe } from "./MeProvider";

function Probe() {
  const me = useMe();
  return <div>{me ? `role:${me.role}` : "role:none"}</div>;
}

describe("MeProvider", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockSession = { accessToken: "token-123" };
  });

  it("fetches /api/me once an access token is available and exposes it via useMe", async () => {
    vi.spyOn(meApi, "getMe").mockResolvedValue({
      user_id: "u1",
      tenant_id: "t1",
      tenant_name: "Clinic",
      email: "doc@example.com",
      role: "doctor",
      branch_id: null,
      branch_name: null,
      permissions: ["conversations:create"],
    });

    render(
      <MeProvider>
        <Probe />
      </MeProvider>
    );

    expect(await screen.findByText("role:doctor")).toBeInTheDocument();
  });

  it("fails closed to null when the fetch errors, rather than granting permissions", async () => {
    vi.spyOn(meApi, "getMe").mockRejectedValue(new Error("network error"));

    render(
      <MeProvider>
        <Probe />
      </MeProvider>
    );

    await waitFor(() => expect(meApi.getMe).toHaveBeenCalled());
    expect(screen.getByText("role:none")).toBeInTheDocument();
  });

  it("does not fetch before a session access token exists", () => {
    mockSession = null;
    const spy = vi.spyOn(meApi, "getMe");

    render(
      <MeProvider>
        <Probe />
      </MeProvider>
    );

    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText("role:none")).toBeInTheDocument();
  });
});
