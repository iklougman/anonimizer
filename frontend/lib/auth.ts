import type { NextAuthOptions } from "next-auth";
import KeycloakProvider from "next-auth/providers/keycloak";
import type { JWT } from "next-auth/jwt";

const KEYCLOAK_ISSUER = process.env.KEYCLOAK_ISSUER!;
const KEYCLOAK_INTERNAL_URL = process.env.KEYCLOAK_INTERNAL_URL!;
const KEYCLOAK_CLIENT_ID = process.env.KEYCLOAK_CLIENT_ID!;
const KEYCLOAK_TOKEN_URL = `${KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/token`;

// 30s of slack before real expiry, so a request in flight doesn't race a token
// that expires mid-call.
const REFRESH_SLACK_MS = 30_000;

async function refreshAccessToken(token: JWT): Promise<JWT> {
  try {
    const response = await fetch(KEYCLOAK_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: KEYCLOAK_CLIENT_ID,
        refresh_token: token.refreshToken ?? "",
      }),
    });
    const refreshed = await response.json();
    if (!response.ok) throw refreshed;

    return {
      ...token,
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      expiresAt: Math.floor(Date.now() / 1000) + refreshed.expires_in,
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshAccessTokenError" };
  }
}

export const authOptions: NextAuthOptions = {
  providers: [
    KeycloakProvider({
      clientId: KEYCLOAK_CLIENT_ID,
      clientSecret: "",
      issuer: KEYCLOAK_ISSUER,
      // Resolved Design Ambiguity #2: explicit endpoints, no auto-discovery.
      // Browser-reachable for the redirect; Docker-internal for server-side calls.
      wellKnown: undefined,
      authorization: {
        url: `${KEYCLOAK_ISSUER}/protocol/openid-connect/auth`,
        params: { scope: "openid email profile" },
      },
      token: KEYCLOAK_TOKEN_URL,
      userinfo: `${KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/userinfo`,
      jwks_endpoint: `${KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/certs`,
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        return {
          ...token,
          accessToken: account.access_token,
          refreshToken: account.refresh_token,
          expiresAt: account.expires_at,
        };
      }

      const expiresAtMs = (token.expiresAt ?? 0) * 1000;
      if (Date.now() < expiresAtMs - REFRESH_SLACK_MS) {
        return token;
      }
      return refreshAccessToken(token);
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken ?? "";
      session.error = token.error;
      return session;
    },
  },
};
