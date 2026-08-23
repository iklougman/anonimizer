# 0023 — Token Vault Scope Generalization

**Status:** Accepted

## Context

ADR-0008 established an isolated Token Vault and ADR-0009 scoped every token to
`(tenant_id, conversation_id)`. The Documents app (Phase 1) needs to pseudonymize
text extracted from an uploaded document, but a document is not a conversation —
there is no `conversation_id` to scope its tokens to, and minting a synthetic
conversation row purely to hang document tokens off of would blur the two
concepts and complicate the conversation-visibility rules in
`app/auth/permissions.py`.

## Decision

`token_mappings` gains a `scope_type` discriminator (`"conversation"` |
`"document"`), `conversation_id` becomes nullable, and a nullable `document_id` is
added with its own composite FK to the new `documents` table. Exactly one of the
two is populated, per `scope_type`, enforced by a `CHECK` constraint. The single
`(tenant_id, conversation_id, token)` unique constraint is replaced by two partial
unique indexes, one per `scope_type` — a plain constraint over sometimes-NULL
columns would not enforce per-scope uniqueness, since Postgres treats every NULL
as distinct.

`TokenVault`, `Pseudonymizer`, `OutputGuard`, and `Pipeline` all change their
scoping parameter from a bare `conversation_id` to `(scope_type, scope_id)`. The
AES-GCM associated data (AAD, see ADR-0009) changes from
`f"{tenant_id}:{conversation_id}:{token}"` to
`f"{tenant_id}:{scope_type}:{scope_id}:{token}"` — binding `scope_type` into the
ciphertext means a document-scoped mapping's ciphertext structurally cannot
decrypt under a conversation-scoped lookup (an `InvalidTag` at decrypt time),
independent of and in addition to the schema-level CHECK constraint and partial
unique indexes.

This is document-local scoping, not identity-linking: one document has its own
scope, and nothing correlates tokens across two different documents, or between a
document and any conversation. It is explicitly **not** the "per-patient scope,
future phase" alternative ADR-0009 considered and deferred — that would let a
token resolve across multiple conversations for the same patient, which is a
different (and larger) design question this change does not touch.

## Alternatives Considered

- **Mint a synthetic `conversation` row per document:** rejected — reuses the
  conversation concept for something that is not a conversation (no messages, no
  visibility-scope semantics, no chat history), and would require either fake
  `Message` rows or forking `list_for_conversation`'s expectations. A first-class
  `scope_type` is a smaller, more honest change.
- **A separate `document_token_mappings` table with duplicated vault logic:**
  rejected — `TokenVault` is the audited, tested implementation of the encryption
  and per-scope isolation contract (ADR-0008); duplicating it for documents would
  create two overlapping implementations of the same security-critical logic, the
  exact failure mode ADR-0004 already rejected for NeMo vs. `privacy_gateway`.
- **Keep `conversation_id` NOT NULL and give every document a real conversation
  row:** rejected for the same reason as the synthetic-row option above, and
  additionally couples document lifecycle to conversation lifecycle (deleting a
  conversation must never be allowed to cascade into deleting a document's
  tokens, and vice versa).

## Consequences

Every existing caller of `TokenVault`/`Pseudonymizer`/`OutputGuard`/`Pipeline` was
updated in the same change to pass `scope_type="conversation"` explicitly — there
is no default, so a future caller must make an explicit, visible choice rather
than silently inheriting conversation scoping for what might be a new kind of
scope. `backend/tests/privacy_invariants/test_token_vault.py` gained explicit
cross-scope isolation tests (a document-scoped token must not resolve as
conversation-scoped, and vice versa) alongside every pre-existing test, updated to
the new call shape rather than left behind.

## Security Implications

Extends ADR-0008's isolation guarantee to a second scope kind without weakening
it: the CHECK constraint, the two partial unique indexes, and the AAD's
`scope_type` binding are three independent enforcement layers (schema shape,
schema uniqueness, and ciphertext authentication) that all have to agree for a
mapping to be readable at all, and any one of them alone is sufficient to reject a
cross-scope read.

## Privacy Implications

No new data is linked across documents or conversations by this change — if
anything, it narrows the existing conversation scope's blast radius by giving
document text an isolated home that a conversation-scoped bug cannot reach, and
vice versa.

## Reversibility

Medium — reversible at the schema level (migration `0009`'s `downgrade()` exists),
but reverting after real document-scoped tokens exist in production would require
either deleting them or migrating them to a conversation scope first; this is the
same reversibility class as ADR-0008 itself.
