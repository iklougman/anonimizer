import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

import * as adminApi from "@/lib/api/admin";
import AdminPermissionsPage from "./page";

const INITIAL_MATRIX = {
  roles: ["doctor", "staff"],
  permissions: ["conversations:read:branch", "conversations:read:all"],
  defaults: { doctor: [], staff: [] },
  matrix: {
    doctor: { "conversations:read:branch": false, "conversations:read:all": false },
    staff: { "conversations:read:branch": false, "conversations:read:all": false },
  },
};

describe("AdminPermissionsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the matrix from the loaded data", async () => {
    vi.spyOn(adminApi, "getPermissionMatrix").mockResolvedValue(INITIAL_MATRIX);

    render(<AdminPermissionsPage />);

    expect(await screen.findByText("Sichtbarkeit Filiale")).toBeInTheDocument();
    expect(screen.getByText("Arzt")).toBeInTheDocument();
    expect(screen.getByText("Personal")).toBeInTheDocument();
  });

  it("toggling a checkbox and saving PUTs the updated matrix", async () => {
    vi.spyOn(adminApi, "getPermissionMatrix").mockResolvedValue(INITIAL_MATRIX);
    const putSpy = vi.spyOn(adminApi, "putPermissionMatrix").mockResolvedValue({
      ...INITIAL_MATRIX,
      matrix: {
        ...INITIAL_MATRIX.matrix,
        doctor: { "conversations:read:branch": true, "conversations:read:all": false },
      },
    });
    const user = userEvent.setup();

    render(<AdminPermissionsPage />);
    await screen.findByText("Sichtbarkeit Filiale");

    const checkboxes = screen.getAllByRole("checkbox");
    // First row (Sichtbarkeit Filiale), first column (Arzt).
    await user.click(checkboxes[0]);
    await user.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith("token-123", {
        doctor: { "conversations:read:branch": true, "conversations:read:all": false },
        staff: { "conversations:read:branch": false, "conversations:read:all": false },
      })
    );
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
  });
});
