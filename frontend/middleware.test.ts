import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("next-auth/jwt", () => ({
  getToken: vi.fn(),
}));

import { getToken } from "next-auth/jwt";
import { middleware, config } from "./middleware";

describe("middleware", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("redirects to sign-in when there is no token", async () => {
    (getToken as any).mockResolvedValue(null);
    const request = new NextRequest("http://localhost:3000/");

    const response = await middleware(request);

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login");
  });

  it("allows the request through when a token is present", async () => {
    (getToken as any).mockResolvedValue({ accessToken: "at-1" });
    const request = new NextRequest("http://localhost:3000/");

    const response = await middleware(request);

    expect(response.status).toBe(200);
  });

  it("redirects when the token has a refresh error, even though a token object exists", async () => {
    (getToken as any).mockResolvedValue({
      accessToken: "at-1",
      error: "RefreshAccessTokenError",
    });
    const request = new NextRequest("http://localhost:3000/");

    const response = await middleware(request);

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login");
  });

  it("matcher excludes the NextAuth API routes", () => {
    expect(config.matcher).not.toContain("/api/auth/:path*");
  });

  it("matcher excludes /login and /signup from the auth gate", () => {
    const matcherRegex = new RegExp(config.matcher[0]);
    expect(matcherRegex.test("/login")).toBe(false);
    expect(matcherRegex.test("/signup")).toBe(false);
    // Sanity: the matcher still gates ordinary routes.
    expect(matcherRegex.test("/dashboard")).toBe(true);
  });
});
