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
    expect(response.headers.get("location")).toContain("/api/auth/signin");
  });

  it("allows the request through when a token is present", async () => {
    (getToken as any).mockResolvedValue({ accessToken: "at-1" });
    const request = new NextRequest("http://localhost:3000/");

    const response = await middleware(request);

    expect(response.status).toBe(200);
  });

  it("matcher excludes the NextAuth API routes", () => {
    expect(config.matcher).not.toContain("/api/auth/:path*");
  });
});
