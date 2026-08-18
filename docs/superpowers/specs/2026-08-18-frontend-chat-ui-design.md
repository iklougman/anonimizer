# Frontend Chat UI — Design

**Status:** Approved for implementation planning
**Date:** 2026-08-18
**Related:** `docs/superpowers/plans/2026-08-17-backend-chat-slice.md`, `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md`, ADR-0021

## 1. Purpose & Scope

The backend chat slice (auth, LLM gateway, conversation CRUD, the send-message
SSE endpoint) is live and merged into `main`. The frontend is still the
original scaffold — a placeholder page and a health check, no auth, no chat
UI. This spec covers building the actual product surface: a doctor logs in
via Keycloak, sees a sidebar of past conversations, picks or starts one, and
sends/receives messages through the existing API.

**In scope:** Keycloak login via NextAuth.js, a two-pane chat shell
(conversation sidebar + active conversation), sending messages and
streaming/rendering the SSE-replayed response, inline error states for the
three failure shapes (422/502/500), a Clinical Calm visual direction.

**Explicitly out of scope:** the dev-only privacy debugger panel (a smaller
follow-up once this core UI exists), e2e/Playwright automation (manual
verification against the live stack, mirroring how the backend plan handled
its E2E check), any UI for switching `LLM_PROVIDER`/tenant admin — those are
operator/deployment concerns, not something a doctor's session touches.

## 2. Architecture & Stack

Next.js 16 App Router (already scaffolded) + NextAuth.js (Auth.js v5) with a
Keycloak OIDC provider. No component library — plain CSS (a global
stylesheet for the Clinical Calm design tokens, CSS Modules for
component-scoped styles), keeping the frontend as dependency-light as the
backend has been (ADR-0016's "thin abstraction over a heavier
framework/library" preference applies here too). The browser talks directly
to the FastAPI backend — no Next.js API proxy layer — matching the master
design doc §3 architecture diagram exactly: browser → Keycloak for login,
browser → FastAPI with the bearer token for everything else. CORS is already
configured backend-side (`CORS_ALLOWED_ORIGINS`) for this.

New dependencies: `next-auth` (Auth.js v5), `@testing-library/react` +
`@testing-library/jest-dom` (dev, for component tests).

## 3. Auth Flow

`app/api/auth/[...nextauth]/route.ts` configures the Keycloak provider:
issuer `KEYCLOAK_ISSUER_URL` (the same public-facing URL the backend
validates the `iss` claim against), client ID `chatgpt-proxy-frontend` — a
public client (`publicClient: true` in `keycloak/realm-export.json`), so no
client secret is configured or needed.

A `jwt` callback copies Keycloak's `access_token`, `refresh_token`, and
`expires_at` onto the NextAuth JWT on sign-in. A `session` callback exposes
the access token as `session.accessToken` — the frontend needs the raw JWT
client-side to send as `Authorization: Bearer <token>` on every backend call,
so it is deliberately not hidden server-side the way a traditional
session-cookie app would. The `tenant_id` claim already baked into the token
by the backend's protocol mapper travels along for free; the frontend never
needs to read or reason about it directly — it's an opaque bearer token as
far as the UI is concerned.

`middleware.ts` protects every route except `/api/auth/*`, redirecting
unauthenticated requests into `next-auth`'s `signIn("keycloak")`, which in
turn redirects to Keycloak's own hosted login page — there is no custom
login form in this app (matches ADR-0021: Keycloak is the identity
provider, not a form this app implements). On token near-expiry, the `jwt`
callback checks `expires_at` and calls Keycloak's token endpoint with the
refresh token before it lapses, so a long-running doctor session doesn't
start silently failing API calls with `401`s mid-conversation.

## 4. Chat UI Structure

```
app/
  (chat)/
    layout.tsx              # sidebar + outlet shell, requires session
    page.tsx                # empty state ("select or start a conversation")
    c/[conversationId]/
      page.tsx               # active conversation view
  api/auth/[...nextauth]/route.ts
components/
  ConversationSidebar.tsx
  MessageBubble.tsx
  Composer.tsx
lib/
  api/
    conversations.ts         # list/create/delete/getMessages
    chat.ts                  # sendMessage() — SSE frame parsing
  auth.ts                    # NextAuth config
```

`ConversationSidebar` calls `GET /api/conversations` on mount and after any
create/delete, renders a "+ Neue Anfrage" button (`POST /api/conversations`,
then client-side navigates to `/c/{new-id}`), and a per-item delete
affordance (`DELETE /api/conversations/{id}`, removed from the list
immediately — optimistic, since a soft-delete on the backend can't
meaningfully fail once the row is confirmed to exist). The active
conversation's title (backend-derived from the first message, truncated) is
shown per sidebar item; conversations without a title yet (empty, just
created) show a placeholder like "Neue Anfrage".

`c/[conversationId]/page.tsx` calls `GET /api/conversations/{id}/messages`
on load to hydrate history (already human-readable — the backend resolves
tokens before returning them, see §5), renders a `MessageBubble` per
message (user right-aligned, assistant left-aligned), and a `Composer` at
the bottom (`textarea`, `Enter` sends, `Shift+Enter` inserts a newline,
disabled while a send is in flight).

## 5. Streaming & Error Handling

The browser's native `EventSource` cannot set an `Authorization` header, so
`lib/api/chat.ts`'s `sendMessage()` uses `fetch()` with the bearer token and
reads `response.body` via `getReader()`, manually parsing `event: token` /
`event: done` frames (`\n\n`-delimited, per the backend's SSE format) and
appending each frame's `delta` to the in-progress assistant bubble as it
arrives.

Because the backend buffers the entire sanitize → LLM → deanonymize
pipeline before emitting the first byte (ADR-0014/0020's "no partial
output" — ​see the backend chat slice design's §4), there is a real wait
before any `token` event arrives. The assistant bubble shows a typing
indicator during that wait, then flips to revealing text the moment the
first `token` event lands — the frontend has no way to distinguish "still
sanitizing" from "still generating," and doesn't need to; both look like
the same typing indicator to the doctor.

**No raw pseudonymization tokens (e.g. `PATIENT_9F2C1`) ever reach this UI
in normal operation** — the backend's `GET .../messages` and the
send-message SSE stream both call `Pipeline.deanonymize()` server-side
before responding, so every string the frontend renders is already
human-readable German text. (A future debugger panel, deferred per §1,
would be the place to surface the tokenized form — out of scope here.)

On a non-2xx response, the assistant bubble is replaced with an inline
error state in place of a reply, matching the design's "stay in context"
principle: the doctor sees exactly which message triggered the failure.

| Status | Copy | Action |
| --- | --- | --- |
| `422` | "This message contains a combination of details that couldn't be safely processed. Try rephrasing with fewer identifying specifics." | none (read `detail` if backend copy changes, don't rely on offering retry — the input itself needs to change) |
| `502` | "The assistant is temporarily unavailable. Try again." | Retry button, resubmits the same user message |
| `500` | "Something went wrong while returning the response. Try again." | Retry button, resubmits the same user message |

The `422` case never gets a bare "retry" button, since retrying an
unmodified message would just fail again — deliberately different from
502/500, both of which are transient-failure shapes where the same message
might succeed a moment later.

## 6. Testing Strategy

Vitest (already the project's runner) + `@testing-library/react` for
component-level tests: `Composer` (Enter sends, Shift+Enter newlines, disabled
while sending), `MessageBubble` error-state rendering for each of the three
failure shapes in the table above, `ConversationSidebar` create/delete
interactions against a mocked API client.

`lib/api/chat.ts`'s SSE frame parser is a pure function over a
`ReadableStream`, so it gets direct unit tests: feed it mocked chunked
`event:`/`data:` frames and assert the delta sequence and final text: a
frame split across multiple `read()` calls (the parser must buffer partial
frames, not assume one chunk = one event); a truncated stream (connection
drops mid-response); a non-2xx JSON error response (no `event:` frames at
all).

No Playwright/e2e in this pass. Real end-to-end verification (real Keycloak
login → real conversation → real streamed reply) is done manually against
the live Docker Compose stack, the same posture the backend chat slice plan
took for its own manual E2E check — keeps this pass's scope to the UI code
itself.
