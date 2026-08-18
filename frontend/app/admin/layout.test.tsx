import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/users",
}));

const mockUseMe = vi.fn();
vi.mock("@/components/MeProvider", () => ({
  useMe: () => mockUseMe(),
}));

import AdminLayout from "./layout";

describe("AdminLayout", () => {
  beforeEach(() => {
    mockUseMe.mockReset();
  });

  it("shows a loading state while me is still resolving", () => {
    mockUseMe.mockReturnValue(null);
    render(<AdminLayout>content</AdminLayout>);
    expect(screen.getByText("Lädt…")).toBeInTheDocument();
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });

  it("denies access to a user with no admin permissions", () => {
    mockUseMe.mockReturnValue({ permissions: ["conversations:create"] });
    render(<AdminLayout>content</AdminLayout>);
    expect(screen.getByText("Kein Zugriff auf die Verwaltung.")).toBeInTheDocument();
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });

  it("renders the admin shell and children for a user with an admin permission", () => {
    mockUseMe.mockReturnValue({ permissions: ["admin:users:manage"] });
    render(<AdminLayout>content</AdminLayout>);
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByText("Benutzer")).toBeInTheDocument();
  });

  it("only shows tabs for permissions the user actually holds", () => {
    mockUseMe.mockReturnValue({ permissions: ["admin:branches:manage"] });
    render(<AdminLayout>content</AdminLayout>);
    expect(screen.getByText("Filialen")).toBeInTheDocument();
    expect(screen.queryByText("Benutzer")).not.toBeInTheDocument();
    expect(screen.queryByText("Berechtigungen")).not.toBeInTheDocument();
  });
});
