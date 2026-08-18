# Frontend Chat UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Also invoke the `impeccable` skill while implementing any visual/CSS work in Tasks 3 and 7** — this plan's CSS is a working starting point (Clinical Calm palette, approved during brainstorming), not a substitute for that skill's polish pass.

**Goal:** Replace the frontend's placeholder page with a real chat application — Keycloak login via NextAuth, a conversation sidebar, and a chat pane that sends messages and streams the SSE-replayed response from the live backend.

**Architecture:** NextAuth.js v4 (stable) with a manually-configured Keycloak OAuth provider (explicit endpoints, not auto-discovery — see Resolved Design Ambiguity #2) issues and refreshes the bearer token the browser sends directly to the FastAPI backend on every request. `middleware.ts` gates all routes except the auth routes. A two-pane shell (`ConversationSidebar` + the active conversation) hydrates from the backend's REST endpoints; sending a message calls the SSE endpoint through a hand-rolled `fetch()`-based stream reader (native `EventSource` can't set an `Authorization` header).

**Tech Stack:** Next.js 16 App Router, React 19, `next-auth@4.24.15`, plain CSS (CSS Modules + a global design-tokens stylesheet, no component library), Vitest + `@testing-library/react` for tests.

## Global Constraints

- **`next-auth@4.24.15` (stable), not the v5 beta.** Verified live: `npm view next-auth dist-tags` shows `latest: 4.24.15`, `beta: 5.0.0-beta.32`. Both declare Next 16/React 19 peer support; v4 is used because this component handles real credentials and a pre-1.0 package is an avoidable risk.
- **Keycloak's issuer must be pinned via `KC_HOSTNAME`, not left to per-request Host-header resolution.** Verified live (see Resolved Design Ambiguity #1): without this, a token issued through the Docker-internal network (`keycloak:8080`, which NextAuth's server-side token exchange uses) carries `iss: http://keycloak:8080/...` instead of `http://localhost:8080/...`, and the backend's `KEYCLOAK_ISSUER_URL` check rejects it.
- **No raw pseudonymization tokens (`PATIENT_9F2C1`-shaped strings) are ever rendered.** The backend's `GET .../messages` and the send-message SSE stream both return already-deanonymized text (see the frontend chat UI design spec §5) — the frontend has no token-rendering code path in this plan.
- **422 never gets a retry button; 502/500 always do.** A 422 means the *content* of the message was rejected — retrying the identical message will fail identically. 502/500 are transient-failure shapes.
- **Clinical Calm palette** (approved during brainstorming): off-white canvas (`#ffffff`/`#f4f7f9`), deep teal accent (`#0f6b5c`), muted borders (`#e2e8ec`). Defined as CSS custom properties in Task 3, used everywhere — no ad hoc colors in component files.
- **The browser calls the FastAPI backend directly** (`NEXT_PUBLIC_API_BASE_URL`), never through a Next.js API proxy route — matches the master design doc §3 architecture diagram and keeps this plan from re-deciding CORS, which is already configured backend-side.
- Local dev assumes the same stack as the backend chat slice plan (`docker compose up -d postgres keycloak ollama`), plus (after Task 1) the Keycloak container recreated with the `KC_HOSTNAME` env vars this plan adds.

## Resolved Design Ambiguities

1. **`KC_HOSTNAME=localhost` (bare hostname, not a URL) + `KC_HOSTNAME_PORT=8080` on the `keycloak` service.** Verified live against a disposable container: `KC_HOSTNAME=http://localhost:8080` (a full URL) produces a malformed issuer (`http://http//localhost:8080:8080/...`) on Keycloak 24 — that URL-accepting syntax is a later "hostname v2" feature, not present in the pinned `quay.io/keycloak/keycloak:24.0` image. The bare-hostname + explicit-port form was confirmed to produce a correctly-pinned `issuer` and a token whose `iss` claim is `http://localhost:8080/realms/chatgpt-proxy-dev` even when the token-issuing request's `Host` header was spoofed to `keycloak:8080` (simulating exactly what NextAuth's server-side token exchange does).
2. **NextAuth's Keycloak provider is configured with explicit `authorization`/`token`/`userinfo`/`jwks_endpoint` URLs and `wellKnown: undefined`, not left to auto-discovery.** Traced through `next-auth`'s actual installed source (`core/lib/oauth/client.js`): when `provider.wellKnown` is falsy, it builds the OIDC issuer directly from `provider.authorization.url` / `provider.token.url` / `provider.userinfo.url` / `provider.jwks_endpoint` with no HTTP discovery call at all — the officially-supported escape hatch for exactly this split-network shape (browser reaches Keycloak via `localhost:8080`; the Next.js server reaches it via the Docker-internal `keycloak:8080`), mirroring the backend's own `keycloak_issuer_url` vs `keycloak_jwks_url` split. Also verified `merge()`'s `for...in` semantics correctly let `wellKnown: undefined` in the passed config null out the provider factory's default `wellKnown` (computed from `issuer`) rather than being ignored.
3. **Error copy comes from the backend's `detail` field; only the retry-button visibility is decided client-side by status code.** The spec's per-status copy table was a first draft; the backend's actual `detail` strings (`"Sensitive information could not be safely processed."` for 422, etc. — see `backend/app/api/chat.py`) are already reasonable user-facing copy, so duplicating slightly-different wording in the frontend would just create two sources of truth to keep in sync. `ErrorBubble` renders whatever `detail` the backend sent; only whether the retry button appears is a frontend decision (`status !== 422`).
4. **`vitest.config.ts`'s `environment` changes from `"node"` to `"jsdom"`.** Needed for `@testing-library/react` (renders into a DOM). The existing `lib/health.test.ts` has no DOM dependency and passes unchanged under `jsdom`.
5. **`KeycloakProvider`'s `clientSecret` is passed as an empty string.** `chatgpt-proxy-frontend` is a public client (`publicClient: true` in `keycloak/realm-export.json`) with no secret; TypeScript's `OAuthUserConfig` shape expects the field to exist. Flagged for live verification at Task 2's login test, not independently confirmed against `openid-client`'s internals the way the two items above were.

---

## Task 1: Pin Keycloak's issuer, install NextAuth, wire environment variables

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `frontend/package.json`, `frontend/package-lock.json`

**Interfaces:**
- Produces: a Keycloak whose `issuer` is `http://localhost:8080/realms/chatgpt-proxy-dev` regardless of which network path reached it; `next-auth@4.24.15` importable in `frontend/`; `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_ISSUER`, `KEYCLOAK_INTERNAL_URL`, `NEXT_PUBLIC_API_BASE_URL` available to the frontend container.

- [ ] **Step 1: Pin the Keycloak hostname in `docker-compose.yml`**

Modify the `keycloak` service's `environment` block:

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    command: start-dev --import-realm
    environment:
      KEYCLOAK_ADMIN: ${KEYCLOAK_ADMIN}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
      KC_HOSTNAME: localhost
      KC_HOSTNAME_PORT: 8080
      KC_HOSTNAME_STRICT_HTTPS: "false"
      KC_HTTP_ENABLED: "true"
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    ports:
      - "127.0.0.1:8080:8080"
```

- [ ] **Step 2: Recreate the Keycloak container and verify the pinned issuer**

Run:
```bash
docker compose up -d --force-recreate keycloak
```
Wait for `Realm 'chatgpt-proxy-dev' imported` in `docker compose logs keycloak`, then:
```bash
curl -s -H "Host: keycloak:8080" http://localhost:8080/realms/chatgpt-proxy-dev/.well-known/openid-configuration | python3 -c "import json,sys; print(json.load(sys.stdin)['issuer'])"
```
Expected: `http://localhost:8080/realms/chatgpt-proxy-dev` — even with the spoofed `Host` header. If this instead prints something containing `keycloak:8080`, the hostname pin did not take effect; do not proceed until it does, since every later task's auth flow depends on this.

- [ ] **Step 3: Add the frontend service's environment variables to `docker-compose.yml`**

Modify the `frontend` service:

```yaml
  frontend:
    build: ./frontend
    environment:
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      KEYCLOAK_CLIENT_ID: chatgpt-proxy-frontend
      KEYCLOAK_ISSUER: http://localhost:8080/realms/chatgpt-proxy-dev
      KEYCLOAK_INTERNAL_URL: http://keycloak:8080/realms/chatgpt-proxy-dev
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000
    ports:
      - "127.0.0.1:${FRONTEND_PORT:-3000}:3000"
    depends_on:
      - backend
      - keycloak
```

- [ ] **Step 4: Document the new env vars in `.env.example`**

Append to `.env.example`:

```
# --- Frontend auth (NextAuth + Keycloak) ---
# Generate with: openssl rand -base64 32
NEXTAUTH_SECRET=change-me-generate-a-real-secret
```

- [ ] **Step 5: Install `next-auth`**

Run:
```bash
cd frontend
npm install next-auth@4.24.15
```
Expected: `package.json` gains `"next-auth": "4.24.15"` under `dependencies`; `npm audit` may report pre-existing `vite`/`esbuild`/`vitest` dev-tooling advisories (unrelated to this install — do not "fix" them as part of this plan).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example frontend/package.json frontend/package-lock.json
git commit -m "feat: pin Keycloak issuer and add NextAuth dependency/env wiring"
```

---

## Task 2: NextAuth configuration, auth route, middleware

**Files:**
- Create: `frontend/lib/auth.ts`
- Create: `frontend/app/api/auth/[...nextauth]/route.ts`
- Create: `frontend/types/next-auth.d.ts`
- Create: `frontend/middleware.ts`
- Test: `frontend/lib/auth.test.ts`
- Test: `frontend/middleware.test.ts`

**Interfaces:**
- Consumes: `KEYCLOAK_ISSUER`, `KEYCLOAK_INTERNAL_URL`, `KEYCLOAK_CLIENT_ID`, `NEXTAUTH_SECRET` (Task 1).
- Produces: `authOptions: NextAuthOptions` (importable from `@/lib/auth`); `Session.accessToken: string`, `Session.error?: string` (module-augmented); middleware protecting all non-auth routes. Consumed by every later task that calls the backend API.

- [ ] **Step 1: Write the failing test for the auth callbacks**

`frontend/lib/auth.test.ts`:

```ts
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
      const session = await authOptions.callbacks!.session!({
        session: { user: {}, expires: "2026-01-01T00:00:00Z" },
        token: { accessToken: "at-1", error: "RefreshAccessTokenError" },
      } as any);

      expect(session.accessToken).toBe("at-1");
      expect(session.error).toBe("RefreshAccessTokenError");
    });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run lib/auth.test.ts`
Expected: FAIL with `Cannot find module './auth'`.

- [ ] **Step 3: Create `frontend/types/next-auth.d.ts`**

```ts
import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    accessToken: string;
    error?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    error?: string;
  }
}
```

- [ ] **Step 4: Create `frontend/lib/auth.ts`**

```ts
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run lib/auth.test.ts`
Expected: PASS (6 passed).

- [ ] **Step 6: Create the NextAuth route handler**

`frontend/app/api/auth/[...nextauth]/route.ts`:

```ts
import NextAuth from "next-auth";
import { authOptions } from "@/lib/auth";

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
```

- [ ] **Step 7: Write the failing middleware test**

`frontend/middleware.test.ts`:

```ts
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
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `cd frontend && npx vitest run middleware.test.ts`
Expected: FAIL with `Cannot find module './middleware'`.

- [ ] **Step 9: Create `frontend/middleware.ts`**

```ts
import { getToken } from "next-auth/jwt";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export async function middleware(request: NextRequest) {
  const token = await getToken({ req: request, secret: process.env.NEXTAUTH_SECRET });

  if (!token) {
    const signInUrl = new URL("/api/auth/signin", request.url);
    signInUrl.searchParams.set("callbackUrl", request.url);
    return NextResponse.redirect(signInUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico).*)"],
};
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `cd frontend && npx vitest run middleware.test.ts`
Expected: PASS (3 passed).

- [ ] **Step 11: Commit**

```bash
git add frontend/lib/auth.ts frontend/lib/auth.test.ts frontend/types/next-auth.d.ts \
  frontend/app/api/auth frontend/middleware.ts frontend/middleware.test.ts
git commit -m "feat: add NextAuth Keycloak config, auth route, and route-protecting middleware"
```

---

## Task 3: Design tokens, global styles, session provider wiring

**Files:**
- Create: `frontend/app/globals.css`
- Modify: `frontend/app/layout.tsx`
- Create: `frontend/components/SessionProviderWrapper.tsx`
- Modify: `frontend/vitest.config.ts`
- Modify: `frontend/package.json` (add `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`)
- Create: `frontend/vitest.setup.ts`

**Interfaces:**
- Produces: CSS custom properties (`--color-bg`, `--color-accent`, etc.) usable by every component's CSS Module; `<SessionProviderWrapper>` wrapping the app so `useSession()` works in client components; a `jsdom` test environment with `@testing-library/jest-dom` matchers.

- [ ] **Step 1: Install the component-testing dependencies**

Run:
```bash
cd frontend
npm install --save-dev @testing-library/react @testing-library/jest-dom jsdom
```

- [ ] **Step 2: Switch the test environment to jsdom**

Replace `frontend/vitest.config.ts` in full:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
```

- [ ] **Step 3: Create the test setup file**

`frontend/vitest.setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Run the existing test suite to verify nothing broke**

Run: `cd frontend && npx vitest run`
Expected: PASS — `lib/health.test.ts` and Task 2's tests all still pass under `jsdom`.

- [ ] **Step 5: Create the design tokens and global styles**

`frontend/app/globals.css`:

```css
:root {
  --color-bg: #ffffff;
  --color-bg-subtle: #f4f7f9;
  --color-border: #e2e8ec;
  --color-border-subtle: #d8e1e4;
  --color-text: #1a2b3c;
  --color-text-muted: #5b6b73;
  --color-text-faint: #8a969b;
  --color-accent: #0f6b5c;
  --color-accent-contrast: #ffffff;
  --color-accent-subtle: #e4eef0;
  --color-error-bg: #fdeceb;
  --color-error-border: #f3c9c6;
  --color-error-text: #9b2c2c;

  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --radius-sm: 6px;
  --radius-md: 10px;
}

* {
  box-sizing: border-box;
}

html,
body {
  height: 100%;
  margin: 0;
  padding: 0;
}

body {
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-sans);
}
```

- [ ] **Step 6: Wire the global stylesheet and SessionProvider into the root layout**

`frontend/components/SessionProviderWrapper.tsx`:

```tsx
"use client";

import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

export function SessionProviderWrapper({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
```

Replace `frontend/app/layout.tsx` in full:

```tsx
import "./globals.css";
import { SessionProviderWrapper } from "@/components/SessionProviderWrapper";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SessionProviderWrapper>{children}</SessionProviderWrapper>
      </body>
    </html>
  );
}
```

- [ ] **Step 7: Run the full suite and a production build**

Run:
```bash
cd frontend
npx vitest run
npm run build
```
Expected: tests pass; the build succeeds (the placeholder `app/page.tsx` still renders — it is replaced in Task 6, not here).

- [ ] **Step 8: Commit**

```bash
git add frontend/app/globals.css frontend/app/layout.tsx frontend/components/SessionProviderWrapper.tsx \
  frontend/vitest.config.ts frontend/vitest.setup.ts frontend/package.json frontend/package-lock.json
git commit -m "feat: add Clinical Calm design tokens and wire SessionProvider"
```

---

## Task 4: API client — types and conversation CRUD

**Files:**
- Create: `frontend/lib/api/types.ts`
- Create: `frontend/lib/api/conversations.ts`
- Test: `frontend/lib/api/conversations.test.ts`

**Interfaces:**
- Produces: `ConversationSummary`, `MessageOut` types; `listConversations(accessToken)`, `createConversation(accessToken)`, `deleteConversation(accessToken, conversationId)`, `getMessages(accessToken, conversationId)`. Consumed by Task 6 (sidebar) and Task 7 (conversation page).

- [ ] **Step 1: Write the failing test**

`frontend/lib/api/conversations.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";
import { listConversations, createConversation, deleteConversation, getMessages } from "./conversations";

describe("conversations API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listConversations sends the bearer token and returns the parsed list", async () => {
    const mockConversations = [
      { id: "1", title: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => mockConversations });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listConversations("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(mockConversations);
  });

  it("createConversation posts and returns the created conversation", async () => {
    const created = { id: "2", title: null, created_at: "x", updated_at: "x" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => created });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createConversation("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations", {
      method: "POST",
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(created);
  });

  it("deleteConversation sends DELETE and resolves on 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 204 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteConversation("token-123", "5")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations/5", {
      method: "DELETE",
      headers: { Authorization: "Bearer token-123" },
    });
  });

  it("getMessages returns the parsed message list", async () => {
    const messages = [{ id: "m1", role: "user", content: "Hallo", created_at: "x" }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => messages });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMessages("token-123", "5");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations/5/messages", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(messages);
  });

  it("listConversations throws on a non-ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations("bad-token")).rejects.toThrow("401");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/conversations.test.ts`
Expected: FAIL with `Cannot find module './conversations'`.

- [ ] **Step 3: Create `frontend/lib/api/types.ts`**

```ts
export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
```

- [ ] **Step 4: Create `frontend/lib/api/conversations.ts`**

```ts
import type { ConversationSummary, MessageOut } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function listConversations(accessToken: string): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_BASE_URL}/api/conversations`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to list conversations: ${response.status}`);
  return response.json();
}

export async function createConversation(accessToken: string): Promise<ConversationSummary> {
  const response = await fetch(`${API_BASE_URL}/api/conversations`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to create conversation: ${response.status}`);
  return response.json();
}

export async function deleteConversation(accessToken: string, conversationId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok && response.status !== 204) {
    throw new Error(`failed to delete conversation: ${response.status}`);
  }
}

export async function getMessages(accessToken: string, conversationId: string): Promise<MessageOut[]> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to load messages: ${response.status}`);
  return response.json();
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/conversations.test.ts`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api/types.ts frontend/lib/api/conversations.ts frontend/lib/api/conversations.test.ts
git commit -m "feat: add conversation CRUD API client"
```

---

## Task 5: SSE frame parser for the send-message endpoint

**Files:**
- Create: `frontend/lib/api/chat.ts`
- Test: `frontend/lib/api/chat.test.ts`

**Interfaces:**
- Consumes: `NEXT_PUBLIC_API_BASE_URL` (Task 1).
- Produces: `ChatApiError` (has `status: number`, `message: string`); `sendMessage(accessToken, conversationId, content, callbacks): Promise<void>` where `callbacks = { onDelta(delta: string): void; onDone(result: { id: string; created_at: string }): void }`. Consumed by Task 7's conversation page.

- [ ] **Step 1: Write the failing test**

`frontend/lib/api/chat.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";
import { sendMessage, ChatApiError } from "./chat";

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index += 1;
      } else {
        controller.close();
      }
    },
  });
}

describe("sendMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("emits deltas in order and calls onDone with the final payload", async () => {
    const body = streamFromChunks([
      'event: token\ndata: {"delta": "Das klingt "}\n\n',
      'event: token\ndata: {"delta": "gut."}\n\n',
      'event: done\ndata: {"id": "m1", "created_at": "2026-01-01T00:00:00Z"}\n\n',
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body }));

    const deltas: string[] = [];
    let done: { id: string; created_at: string } | undefined;

    await sendMessage("token-123", "conv-1", "Hallo", {
      onDelta: (delta) => deltas.push(delta),
      onDone: (result) => {
        done = result;
      },
    });

    expect(deltas).toEqual(["Das klingt ", "gut."]);
    expect(done).toEqual({ id: "m1", created_at: "2026-01-01T00:00:00Z" });
  });

  it("buffers a frame split across multiple stream chunks", async () => {
    const body = streamFromChunks([
      'event: token\ndata: {"delta": "Hallo"',
      "}\n\n",
      'event: done\ndata: {"id": "m1", "created_at": "x"}\n\n',
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body }));

    const deltas: string[] = [];
    await sendMessage("token-123", "conv-1", "Hallo", {
      onDelta: (delta) => deltas.push(delta),
      onDone: () => {},
    });

    expect(deltas).toEqual(["Hallo"]);
  });

  it("sends the message content as a JSON body with the bearer token", async () => {
    const body = streamFromChunks(['event: done\ndata: {"id": "m1", "created_at": "x"}\n\n']);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body });
    vi.stubGlobal("fetch", fetchMock);

    await sendMessage("token-123", "conv-1", "Hallo Welt", { onDelta: () => {}, onDone: () => {} });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/conversations/conv-1/messages",
      {
        method: "POST",
        headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
        body: JSON.stringify({ content: "Hallo Welt" }),
      }
    );
  });

  it("throws a ChatApiError with the backend detail on a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: "Sensitive information could not be safely processed." }),
      })
    );

    await expect(
      sendMessage("token-123", "conv-1", "risky message", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toMatchObject({
      status: 422,
      message: "Sensitive information could not be safely processed.",
    });
  });

  it("throws a ChatApiError with a generic message when the error response isn't JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json");
        },
      })
    );

    await expect(
      sendMessage("token-123", "conv-1", "hi", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toMatchObject({ status: 502, message: "Something went wrong. Try again." });
  });

  it("is an instance of ChatApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    );

    await expect(
      sendMessage("token-123", "conv-1", "hi", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toBeInstanceOf(ChatApiError);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/chat.test.ts`
Expected: FAIL with `Cannot find module './chat'`.

- [ ] **Step 3: Create `frontend/lib/api/chat.ts`**

```ts
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface SendMessageCallbacks {
  onDelta: (delta: string) => void;
  onDone: (result: { id: string; created_at: string }) => void;
}

export class ChatApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ChatApiError";
    this.status = status;
  }
}

export async function sendMessage(
  accessToken: string,
  conversationId: string,
  content: string,
  callbacks: SendMessageCallbacks
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });

  if (!response.ok) {
    let message = "Something went wrong. Try again.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // response body wasn't JSON; keep the generic message
    }
    throw new ChatApiError(response.status, message);
  }

  if (!response.body) {
    throw new ChatApiError(response.status, "Empty response stream");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      processFrame(frame, callbacks);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function processFrame(frame: string, callbacks: SendMessageCallbacks): void {
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event: "));
  const dataLine = lines.find((line) => line.startsWith("data: "));
  if (!eventLine || !dataLine) return;

  const event = eventLine.slice("event: ".length);
  const data = JSON.parse(dataLine.slice("data: ".length));

  if (event === "token") {
    callbacks.onDelta(data.delta);
  } else if (event === "done") {
    callbacks.onDone(data);
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/chat.test.ts`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/chat.ts frontend/lib/api/chat.test.ts
git commit -m "feat: add the SSE frame parser for the send-message endpoint"
```

---

## Task 6: Chat shell — conversation sidebar and routing

**Files:**
- Create: `frontend/components/ConversationSidebar.tsx`
- Create: `frontend/components/ConversationSidebar.module.css`
- Create: `frontend/app/(chat)/layout.tsx`
- Create: `frontend/app/(chat)/page.tsx`
- Modify: `frontend/app/page.tsx` (redirect to `/`'s replacement — see Step 6)
- Test: `frontend/components/ConversationSidebar.test.tsx`

**Interfaces:**
- Consumes: `listConversations`, `createConversation`, `deleteConversation` (Task 4); `useSession` (`next-auth/react`, Task 3's provider).
- Produces: `<ConversationSidebar activeConversationId?: string>`. Consumed by Task 7's conversation-page layout (already wired here) and no other task.

- [ ] **Step 1: Write the failing test**

`frontend/components/ConversationSidebar.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

const mockRouterPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

import * as conversationsApi from "@/lib/api/conversations";
import { ConversationSidebar } from "./ConversationSidebar";

describe("ConversationSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockRouterPush.mockReset();
  });

  it("lists conversations on mount", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: "Anfrage Patient A", created_at: "x", updated_at: "x" },
    ]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("Anfrage Patient A")).toBeInTheDocument();
  });

  it("shows a placeholder title for untitled conversations", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: null, created_at: "x", updated_at: "x" },
    ]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("Neue Anfrage")).toBeInTheDocument();
  });

  it("creates a conversation and navigates to it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([]);
    vi.spyOn(conversationsApi, "createConversation").mockResolvedValue({
      id: "new-1",
      title: null,
      created_at: "x",
      updated_at: "x",
    });
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await user.click(await screen.findByText("+ Neue Anfrage"));

    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith("/c/new-1"));
  });

  it("removes a conversation from the list after deleting it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: "Anfrage Patient A", created_at: "x", updated_at: "x" },
    ]);
    vi.spyOn(conversationsApi, "deleteConversation").mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await screen.findByText("Anfrage Patient A");
    await user.click(screen.getByLabelText("Delete Anfrage Patient A"));

    await waitFor(() => expect(screen.queryByText("Anfrage Patient A")).not.toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run components/ConversationSidebar.test.tsx`
Expected: FAIL — `Cannot find module '@testing-library/user-event'` first (installed in Step 3), then `Cannot find module './ConversationSidebar'`.

- [ ] **Step 3: Install `@testing-library/user-event`**

Run:
```bash
cd frontend
npm install --save-dev @testing-library/user-event
```

- [ ] **Step 4: Run the test again to confirm it now fails on the missing component**

Run: `cd frontend && npx vitest run components/ConversationSidebar.test.tsx`
Expected: FAIL with `Cannot find module './ConversationSidebar'`.

- [ ] **Step 5: Create `frontend/components/ConversationSidebar.module.css`**

```css
.sidebar {
  width: 280px;
  flex-shrink: 0;
  background: var(--color-bg-subtle);
  border-right: 1px solid var(--color-border);
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.newButton {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border: none;
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  margin-bottom: 12px;
  text-align: left;
}

.newButton:hover {
  opacity: 0.92;
}

.conversationItem {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 14px;
  gap: 8px;
}

.conversationItem:hover {
  background: var(--color-accent-subtle);
}

.conversationItemActive {
  background: var(--color-accent-subtle);
  color: var(--color-text);
  font-weight: 600;
}

.conversationTitle {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deleteButton {
  background: none;
  border: none;
  color: var(--color-text-faint);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

.deleteButton:hover {
  color: var(--color-error-text);
  background: var(--color-error-bg);
}
```

- [ ] **Step 6: Create `frontend/components/ConversationSidebar.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import {
  createConversation,
  deleteConversation,
  listConversations,
} from "@/lib/api/conversations";
import type { ConversationSummary } from "@/lib/api/types";
import styles from "./ConversationSidebar.module.css";

export function ConversationSidebar({ activeConversationId }: { activeConversationId?: string }) {
  const { data: session } = useSession();
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);

  useEffect(() => {
    if (!session?.accessToken) return;
    listConversations(session.accessToken).then(setConversations);
  }, [session?.accessToken]);

  async function handleCreate() {
    if (!session?.accessToken) return;
    const created = await createConversation(session.accessToken);
    setConversations((current) => [created, ...current]);
    router.push(`/c/${created.id}`);
  }

  async function handleDelete(conversationId: string, title: string) {
    if (!session?.accessToken) return;
    await deleteConversation(session.accessToken, conversationId);
    setConversations((current) => current.filter((c) => c.id !== conversationId));
    if (activeConversationId === conversationId) {
      router.push("/");
    }
  }

  return (
    <nav className={styles.sidebar}>
      <button type="button" className={styles.newButton} onClick={handleCreate}>
        + Neue Anfrage
      </button>
      {conversations.map((conversation) => {
        const title = conversation.title ?? "Neue Anfrage";
        return (
          <div
            key={conversation.id}
            className={
              conversation.id === activeConversationId
                ? `${styles.conversationItem} ${styles.conversationItemActive}`
                : styles.conversationItem
            }
            onClick={() => router.push(`/c/${conversation.id}`)}
          >
            <span className={styles.conversationTitle}>{title}</span>
            <button
              type="button"
              aria-label={`Delete ${title}`}
              className={styles.deleteButton}
              onClick={(event) => {
                event.stopPropagation();
                handleDelete(conversation.id, title);
              }}
            >
              ×
            </button>
          </div>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd frontend && npx vitest run components/ConversationSidebar.test.tsx`
Expected: PASS (4 passed).

- [ ] **Step 8: Create the `(chat)` route group layout and empty-state page**

`frontend/app/(chat)/layout.tsx`:

```tsx
import type { ReactNode } from "react";
import { ConversationSidebar } from "@/components/ConversationSidebar";
import styles from "./layout.module.css";

export default function ChatLayout({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <ConversationSidebar />
      <main className={styles.main}>{children}</main>
    </div>
  );
}
```

`frontend/app/(chat)/layout.module.css`:

```css
.shell {
  display: flex;
  height: 100vh;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
```

`frontend/app/(chat)/page.tsx`:

```tsx
export default function ChatEmptyState() {
  return (
    <div style={{ margin: "auto", color: "var(--color-text-faint)", fontSize: 15 }}>
      Select a conversation or start a new one.
    </div>
  );
}
```

- [ ] **Step 9: Replace the placeholder root page**

The old placeholder at `frontend/app/page.tsx` conflicts with the new `(chat)` route group's own `page.tsx` for the `/` route (route groups don't add a URL segment, so `app/(chat)/page.tsx` already serves `/`). Delete `frontend/app/page.tsx`:

```bash
rm frontend/app/page.tsx
```

- [ ] **Step 10: Run the full suite and a production build**

Run:
```bash
cd frontend
npx vitest run
npm run build
```
Expected: all tests pass; the build succeeds with `/` now serving the chat empty state (behind `middleware.ts`'s auth redirect).

- [ ] **Step 11: Commit**

```bash
git add frontend/components/ConversationSidebar.tsx frontend/components/ConversationSidebar.module.css \
  frontend/components/ConversationSidebar.test.tsx frontend/app/\(chat\) frontend/package.json \
  frontend/package-lock.json
git rm frontend/app/page.tsx
git commit -m "feat: add the conversation sidebar and chat route shell"
```

---

## Task 7: Message rendering, composer, error states, conversation page

**Files:**
- Create: `frontend/components/MessageBubble.tsx`
- Create: `frontend/components/MessageBubble.module.css`
- Create: `frontend/components/Composer.tsx`
- Create: `frontend/components/Composer.module.css`
- Create: `frontend/app/(chat)/c/[conversationId]/page.tsx`
- Test: `frontend/components/MessageBubble.test.tsx`
- Test: `frontend/components/Composer.test.tsx`

**Interfaces:**
- Consumes: `getMessages` (Task 4); `sendMessage`, `ChatApiError` (Task 5); `ConversationSidebar` (Task 6, already wired via the shared layout).
- Produces: the fully wired conversation view. Terminal task — nothing later consumes these.

- [ ] **Step 1: Write the failing `MessageBubble` test**

`frontend/components/MessageBubble.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageBubble } from "./MessageBubble";

describe("MessageBubble", () => {
  it("renders a user message right-aligned", () => {
    render(<MessageBubble message={{ id: "1", role: "user", content: "Hallo", created_at: "x" }} />);
    expect(screen.getByText("Hallo")).toBeInTheDocument();
  });

  it("renders an assistant message", () => {
    render(
      <MessageBubble message={{ id: "1", role: "assistant", content: "Guten Tag", created_at: "x" }} />
    );
    expect(screen.getByText("Guten Tag")).toBeInTheDocument();
  });

  it("renders a 422 error with no retry button", () => {
    render(
      <MessageBubble
        error={{ status: 422, message: "Sensitive information could not be safely processed." }}
        onRetry={() => {}}
      />
    );
    expect(
      screen.getByText("Sensitive information could not be safely processed.")
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("renders a 502 error with a retry button that calls onRetry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <MessageBubble error={{ status: 502, message: "Provider unavailable" }} onRetry={onRetry} />
    );

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders a 500 error with a retry button", () => {
    render(<MessageBubble error={{ status: 500, message: "Something went wrong" }} onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run components/MessageBubble.test.tsx`
Expected: FAIL with `Cannot find module './MessageBubble'`.

- [ ] **Step 3: Create `frontend/components/MessageBubble.module.css`**

```css
.row {
  display: flex;
  margin-bottom: 12px;
}

.rowUser {
  justify-content: flex-end;
}

.rowAssistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  line-height: 1.5;
  font-size: 14.5px;
  white-space: pre-wrap;
}

.bubbleUser {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}

.bubbleAssistant {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.bubbleError {
  background: var(--color-error-bg);
  border: 1px solid var(--color-error-border);
  color: var(--color-error-text);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.retryButton {
  background: none;
  border: 1px solid var(--color-error-text);
  color: var(--color-error-text);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 13px;
  cursor: pointer;
}

.retryButton:hover {
  background: var(--color-error-text);
  color: #fff;
}
```

- [ ] **Step 4: Create `frontend/components/MessageBubble.tsx`**

```tsx
import type { MessageOut } from "@/lib/api/types";
import styles from "./MessageBubble.module.css";

interface MessageErrorProps {
  status: number;
  message: string;
}

interface MessageBubbleProps {
  message?: MessageOut;
  error?: MessageErrorProps;
  onRetry?: () => void;
}

export function MessageBubble({ message, error, onRetry }: MessageBubbleProps) {
  if (error) {
    const canRetry = error.status !== 422;
    return (
      <div className={`${styles.row} ${styles.rowAssistant}`}>
        <div className={`${styles.bubble} ${styles.bubbleError}`} role="alert">
          <p>{error.message}</p>
          {canRetry && (
            <button type="button" className={styles.retryButton} onClick={onRetry}>
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!message) return null;

  const isUser = message.role === "user";
  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowAssistant}`}>
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
        {message.content}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run components/MessageBubble.test.tsx`
Expected: PASS (5 passed).

- [ ] **Step 6: Write the failing `Composer` test**

`frontend/components/Composer.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "./Composer";

describe("Composer", () => {
  it("sends the message on Enter and clears the input", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "Hallo{Enter}");

    expect(onSend).toHaveBeenCalledWith("Hallo");
    expect(textarea).toHaveValue("");
  });

  it("inserts a newline on Shift+Enter instead of sending", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "Zeile 1{Shift>}{Enter}{/Shift}Zeile 2");

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("Zeile 1\nZeile 2");
  });

  it("does not send an empty or whitespace-only message", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables the textarea and send button while disabled", () => {
    render(<Composer onSend={() => {}} disabled={true} />);

    expect(screen.getByPlaceholderText("Nachricht eingeben…")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `cd frontend && npx vitest run components/Composer.test.tsx`
Expected: FAIL with `Cannot find module './Composer'`.

- [ ] **Step 8: Create `frontend/components/Composer.module.css`**

```css
.composer {
  display: flex;
  gap: 8px;
  padding: 16px;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg);
}

.textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  color: var(--color-text);
  font-family: var(--font-sans);
  font-size: 14.5px;
  padding: 10px 12px;
  line-height: 1.5;
  min-height: 44px;
  max-height: 200px;
}

.textarea:disabled {
  color: var(--color-text-faint);
}

.sendButton {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border: none;
  border-radius: var(--radius-sm);
  padding: 0 18px;
  font-weight: 600;
  cursor: pointer;
}

.sendButton:disabled {
  background: var(--color-border-subtle);
  color: var(--color-text-faint);
  cursor: not-allowed;
}
```

- [ ] **Step 9: Create `frontend/components/Composer.tsx`**

```tsx
"use client";

import { useState } from "react";
import type { KeyboardEvent } from "react";
import styles from "./Composer.module.css";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (content: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className={styles.composer}>
      <textarea
        className={styles.textarea}
        placeholder="Nachricht eingeben…"
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button type="button" className={styles.sendButton} disabled={disabled} onClick={submit}>
        Send
      </button>
    </div>
  );
}
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `cd frontend && npx vitest run components/Composer.test.tsx`
Expected: PASS (4 passed).

- [ ] **Step 11: Create the conversation page**

`frontend/app/(chat)/c/[conversationId]/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { getMessages } from "@/lib/api/conversations";
import { sendMessage, ChatApiError } from "@/lib/api/chat";
import type { MessageOut } from "@/lib/api/types";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import styles from "./page.module.css";

type DisplayItem =
  | { kind: "message"; message: MessageOut }
  | { kind: "error"; status: number; message: string; retryContent: string };

export default function ConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const { data: session } = useSession();
  const [items, setItems] = useState<DisplayItem[]>([]);
  const [pendingAssistantText, setPendingAssistantText] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!session?.accessToken) return;
    getMessages(session.accessToken, conversationId).then((messages) => {
      setItems(messages.map((message) => ({ kind: "message", message })));
    });
  }, [session?.accessToken, conversationId]);

  async function handleSend(content: string) {
    if (!session?.accessToken) return;
    setItems((current) => [
      ...current,
      { kind: "message", message: { id: `pending-${Date.now()}`, role: "user", content, created_at: "" } },
    ]);
    setSending(true);
    setPendingAssistantText("");
    // A local accumulator, not the pendingAssistantText state: onDone's closure
    // over React state would capture the value from when handleSend was called,
    // not the deltas onDelta accumulates during the same call -- state updates
    // don't propagate into an already-created closure. onDelta and onDone run
    // synchronously in sequence inside sendMessage's stream-reading loop, so a
    // plain local variable shared by both closures is correct here.
    let accumulatedText = "";

    try {
      await sendMessage(session.accessToken, conversationId, content, {
        onDelta: (delta) => {
          accumulatedText += delta;
          setPendingAssistantText(accumulatedText);
        },
        onDone: (result) => {
          setItems((current) => [
            ...current,
            {
              kind: "message",
              message: {
                id: result.id,
                role: "assistant",
                content: accumulatedText,
                created_at: result.created_at,
              },
            },
          ]);
          setPendingAssistantText(null);
        },
      });
    } catch (error) {
      const chatError =
        error instanceof ChatApiError ? error : new ChatApiError(500, "Something went wrong. Try again.");
      setItems((current) => [
        ...current,
        { kind: "error", status: chatError.status, message: chatError.message, retryContent: content },
      ]);
      setPendingAssistantText(null);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <div className={styles.messages}>
        {items.map((item, index) =>
          item.kind === "message" ? (
            <MessageBubble key={item.message.id} message={item.message} />
          ) : (
            <MessageBubble
              key={`error-${index}`}
              error={{ status: item.status, message: item.message }}
              onRetry={() => handleSend(item.retryContent)}
            />
          )
        )}
        {sending && pendingAssistantText !== null && (
          <MessageBubble
            message={{ id: "pending", role: "assistant", content: pendingAssistantText || "…", created_at: "" }}
          />
        )}
      </div>
      <Composer onSend={handleSend} disabled={sending} />
    </>
  );
}
```

`frontend/app/(chat)/c/[conversationId]/page.module.css`:

```css
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
}
```

- [ ] **Step 12: Run the full suite and a production build**

Run:
```bash
cd frontend
npx vitest run
npm run build
```
Expected: all tests pass (test count should be the sum of every task's tests: 6 + 3 + 5 + 6 + 4 + 5 + 4 = 33, plus the pre-existing `lib/health.test.ts`); the build succeeds.

- [ ] **Step 13: Commit**

```bash
git add frontend/components/MessageBubble.tsx frontend/components/MessageBubble.module.css \
  frontend/components/MessageBubble.test.tsx frontend/components/Composer.tsx \
  frontend/components/Composer.module.css frontend/components/Composer.test.tsx \
  "frontend/app/(chat)/c"
git commit -m "feat: add message rendering, composer, and the wired conversation page"
```

---

## Task 8: Manual E2E verification and documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: a documented, runnable manual check; no new code.

- [ ] **Step 1: Document the manual frontend E2E flow in `README.md`**

Under `## Frontend tests`, append:

    ### Manual end-to-end check

    ```bash
    docker compose up -d --build frontend
    open http://localhost:3000
    ```

    Expected: redirected to Keycloak's hosted login page (not a custom form).
    Log in as `dr.mueller` / `dev-password`. You should land on the chat shell
    with an empty sidebar. Click "+ Neue Anfrage", send a message, and confirm:

    - A typing indicator shows while the backend buffers sanitize → LLM →
      deanonymize (no partial/raw text appears before the first `token` event).
    - The reply streams in word-by-word once it starts.
    - The conversation gets a title (derived from your first message) after
      the first exchange, and appears in the sidebar.
    - Refreshing the page keeps you logged in and reloads the conversation
      history correctly (proves the NextAuth session and `GET .../messages`
      round-trip both work).

    To see the 422 fail-closed path, send a message combining several
    identifying details (age + city + a rare disease name + a date) and
    confirm it renders as an inline error with no retry button.

- [ ] **Step 2: Run the full suite and a production build one last time**

Run:
```bash
cd frontend
npx vitest run
npm run build
```
Expected: all tests pass; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the manual frontend end-to-end check"
```

---

## Post-Plan State

After Task 8, `frontend/` gains:

```
app/
  (chat)/
    layout.tsx / layout.module.css
    page.tsx
    c/[conversationId]/page.tsx / page.module.css
  api/auth/[...nextauth]/route.ts
  globals.css
  layout.tsx           # SessionProviderWrapper + globals.css
components/
  ConversationSidebar.tsx / .module.css
  MessageBubble.tsx / .module.css
  Composer.tsx / .module.css
  SessionProviderWrapper.tsx
lib/
  auth.ts
  api/
    types.ts
    conversations.ts
    chat.ts
middleware.ts
types/next-auth.d.ts
```

plus the Keycloak hostname pin and NextAuth environment wiring in
`docker-compose.yml`/`.env.example`.

**Deliberately not built here:** the dev-only privacy debugger panel
(deferred per the design spec §1), Playwright/e2e automation, any UI for
switching `LLM_PROVIDER`/tenant admin.

**Known follow-ups this plan surfaces:**
- `KeycloakProvider`'s `clientSecret: ""` for a public client is assumed
  correct, not independently verified against `openid-client`'s internals
  the way the issuer-pinning and manual-endpoints fixes were (Resolved
  Design Ambiguity #5) — Task 2's live login test is the real check.
- The visual design (Clinical Calm tokens, component CSS) is a working
  starting point approved during brainstorming; running it through the
  `impeccable` skill's polish pass, per this plan's header note, may
  surface refinements Tasks 3/6/7 didn't anticipate.
- Automated realm-per-tenant provisioning and the privacy debugger panel
  remain open items from the backend chat slice plan and design spec
  respectively — neither is addressed by frontend work.
