# Message Flow (High-Level)

## In words

1. The user types a message in the frontend and hits send.
2. The frontend sends it to the backend with the user's Keycloak JWT.
3. The backend verifies the JWT and resolves the tenant + user.
4. Before anything else, the message is **sanitized**: personal data (names,
   dates, locations, IDs) is detected and replaced with anonymous tokens like
   `PERSON_A1B2C3`.
5. The tokenized message is saved to the database and sent to the LLM provider
   (Ollama or OpenAI). The LLM only ever sees the tokens, never the real names.
6. The LLM replies — also in token form, because it was told to preserve them.
7. The reply is **deanonymized**: tokens are swapped back for the original
   values, which only the system knows.
8. The tokenized reply is saved to the database (storage never holds raw text).
9. The reconstructed human-readable reply is streamed back to the frontend as
   SSE events and rendered for the user.

The key invariant: **raw personal data never leaves the backend**. It is never
sent to the LLM and never stored. Only tokens cross those boundaries; the real
values are restored on the way out using a per-tenant key.

## Terminal diagram

```
  User                Frontend              Backend API
 ┌────┐   types msg   ┌───────┐   POST +JWT  ┌──────────────┐
 │ 👤 │ ────────────> │  web  │ ──────────> │ /api/messages │
 └────┘               └───────┘              └──────┬───────┘
       ▲                                            │
       │  render                                     │ 1. verify JWT + tenant
       │                                             ▼
       │                                       ┌───────────┐
       │                                       │ Sanitize  │  detect PII
       │                                       │ replace → │  mint tokens
       │                                       │  tokens   │  (AES-GCM)
       │                                       └─────┬─────┘
       │                                  422 if risk │ too high → fail-closed
       │                                             ▼
       │                                       ┌───────────┐
       │                                       │    DB     │  save tokenized
       │                                       └─────┬─────┘  message
       │                                             ▼
       │     ┌─────────────────────────────────────────────┐
       │     │  LLM Provider (Ollama / OpenAI)  stream:false │  sees only
       │     │   system: "preserve tokens verbatim"          │  tokens
       │     └──────────────────────┬──────────────────────┘
       │                            │ token-shaped reply
       │                            ▼
       │                                       ┌───────────┐
       │                                       │Deanonymize│  tokens → PII
       │                                       │ restore   │  (per-tenant key)
       │                                       └─────┬─────┘
       │                                             ▼
       │                                       ┌───────────┐
       │                                       │    DB     │  save tokenized
       │                                       └─────┬─────┘  reply
       │                                             ▼
       │                                       ┌───────────┐
       │  SSE stream  <─────────────────────── │  replay   │  text/event-stream
       │  (token chunks)                        └───────────┘
       └──────────────────────────────────────────
```