import { beforeEach, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { signOut, useSession } from "next-auth/react";
import { SessionErrorHandler } from "./SessionErrorHandler";

vi.mock("next-auth/react", () => ({ useSession: vi.fn(), signOut: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
});

it("signs out with a /login callback when the session has a refresh error", () => {
  (useSession as any).mockReturnValue({ data: { error: "RefreshAccessTokenError" } });
  render(<SessionErrorHandler />);
  expect(signOut).toHaveBeenCalledWith({ callbackUrl: "/login" });
});

it("does nothing when there is no session error", () => {
  (useSession as any).mockReturnValue({ data: { error: undefined } });
  render(<SessionErrorHandler />);
  expect(signOut).not.toHaveBeenCalled();
});
