import { encode } from "next-auth/jwt";
import type { BrowserContext } from "@playwright/test";

const KEYCLOAK_ISSUER = process.env.KEYCLOAK_ISSUER ?? "http://localhost:8080/realms/chatgpt-proxy-dev";
const NEXTAUTH_SECRET = process.env.NEXTAUTH_SECRET;

export interface RopcTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // unix seconds, matching frontend/lib/auth.ts's jwt() callback shape
}

export async function getRopcTokens(username: string, password: string): Promise<RopcTokens> {
  const response = await fetch(`${KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "chatgpt-proxy-frontend",
      username,
      password,
    }),
  });
  if (!response.ok) {
    throw new Error(`ROPC token request failed for ${username}: ${response.status} ${await response.text()}`);
  }
  const body = await response.json();
  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Math.floor(Date.now() / 1000) + body.expires_in,
  };
}

/**
 * Injects a valid NextAuth session cookie directly, bypassing Keycloak's
 * hosted login UI -- for journeys where the point is what happens *after*
 * login (see e2e/tests/login.spec.ts for the one journey that drives the
 * real UI instead). Builds the same token shape frontend/lib/auth.ts's
 * jwt() callback produces and encodes it with next-auth/jwt's encode(),
 * matching the plain-HTTP (no __Secure- prefix) cookie name middleware.ts
 * reads via getToken().
 */
export async function injectSession(context: BrowserContext, tokens: RopcTokens): Promise<void> {
  if (!NEXTAUTH_SECRET) {
    throw new Error("NEXTAUTH_SECRET must be set for injectSession() to encode a valid session cookie");
  }
  const token = {
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    expiresAt: tokens.expiresAt,
  };
  const encoded = await encode({ token, secret: NEXTAUTH_SECRET });
  const url = new URL(process.env.E2E_BASE_URL ?? "http://localhost:3000");
  await context.addCookies([
    {
      name: "next-auth.session-token",
      value: encoded,
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    },
  ]);
}
