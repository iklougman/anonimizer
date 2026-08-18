import { describe, expect, it, vi, beforeEach } from "vitest";
import { authOptions } from "./auth";

describe("authOptions", () => {
  it("configures the Keycloak provider with explicit, non-discovery endpoints", () => {
    const provider = authOptions.providers[0] as any;
    expect(provider.id).toBe("keycloak");
    expect(provider.options.wellKnown).toBeUndefined();
    expect(provider.options.authorization.url).toBe(
      "http://localhost:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/auth"
    );
    expect(provider.options.token).toBe(
      "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/token"
    );
  });

  describe("jwt callback", () => {
    it("stores the access token, refresh token, and expiry on initial sign-in", async () => {
      const token = await authOptions.callbacks!.jwt!({
        token: {},
        account: {
          access_token: "at-1",
          refresh_token: "rt-1",
          expires_at: 1_800_000_000,
        },
      } as any);

      expect(token).toMatchObject({
        accessToken: "at-1",
        refreshToken: "rt-1",
        expiresAt: 1_800_000_000,
      });
    });

    it("returns the existing token unchanged when it is not close to expiry", async () => {
      const farFuture = Math.floor(Date.now() / 1000) + 3600;
      const existingToken = { accessToken: "at-1", refreshToken: "rt-1", expiresAt: farFuture };

      const token = await authOptions.callbacks!.jwt!({
        token: existingToken,
        account: null,
      } as any);

      expect(token).toEqual(existingToken);
    });

    it("refreshes the token when it is close to expiry", async () => {
      const almostExpired = Math.floor(Date.now() / 1000) + 10;
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          access_token: "at-2",
          refresh_token: "rt-2",
          expires_in: 300,
        }),
      });
      vi.stubGlobal("fetch", fetchMock);

      const token = await authOptions.callbacks!.jwt!({
        token: { accessToken: "at-1", refreshToken: "rt-1", expiresAt: almostExpired },
        account: null,
      } as any);

      expect(fetchMock).toHaveBeenCalledWith(
        "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/token",
        expect.objectContaining({ method: "POST" })
      );
      expect(token.accessToken).toBe("at-2");
      expect(token.refreshToken).toBe("rt-2");
      vi.unstubAllGlobals();
    });

    it("marks the token with an error when refresh fails", async () => {
      const almostExpired = Math.floor(Date.now() / 1000) + 10;
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));

      const token = await authOptions.callbacks!.jwt!({
        token: { accessToken: "at-1", refreshToken: "rt-1", expiresAt: almostExpired },
        account: null,
      } as any);

      expect(token.error).toBe("RefreshAccessTokenError");
      vi.unstubAllGlobals();
    });
  });

  describe("session callback", () => {
    it("exposes the access token and error on the session", async () => {
      // next-auth v4's own callback type declares the return as
      // `Session | DefaultSession` regardless of our module augmentation
      // (DefaultSession has neither field), so the result is cast for the
      // property checks below -- lib/auth.ts's implementation always returns
      // the full Session shape.
      const session = (await authOptions.callbacks!.session!({
        session: { user: {}, expires: "2026-01-01T00:00:00Z" },
        token: { accessToken: "at-1", error: "RefreshAccessTokenError" },
      } as any)) as any;

      expect(session.accessToken).toBe("at-1");
      expect(session.error).toBe("RefreshAccessTokenError");
    });
  });
});
