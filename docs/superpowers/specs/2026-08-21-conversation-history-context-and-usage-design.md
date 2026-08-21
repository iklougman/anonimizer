# Conversation history, context-window management, and inline usage

**Status:** Draft
**Date:** 2026-08-21

## Goal

Today the chat endpoint sends only the latest sanitized message to the model —
`provider.complete(sanitized_prompt)` — so the model has no memory of prior turns.
This spec adds: full conversation history on every send; per-conversation model
selection; context-window awareness that auto-trims to the latest messages when
the window is full and proposes a larger model; and an inline per-conversation
view of token usage and cost.

## Non-goals

- No real-tokenizer integration (tiktoken / Ollama `/api/tokenize`). Token counts
  used for the trim decision are a conservative heuristic estimate; the
  provider-reported `tokens_in`/`tokens_out` already stored on `llm_requests`
  remain the authoritative numbers for the usage view.
- No admin/tenant-wide usage dashboard. Usage is shown inline, per conversation,
  to anyone who can read that conversation. (A tenant-wide admin dashboard is a
  separate future task.)
- No streaming of real LLM tokens. The existing fake "replay as SSE" streaming
  behavior is preserved; only the `done` event's payload is extended.
- No change to the privacy boundary: the model still only ever sees pseudonymized
  `sanitized_content`. Replaying history sends the same pseudonymized text that
  was already approved to leave the boundary on its original turn.

## Background

- The privacy pipeline sanitizes each user message before it reaches the provider
  and deanonymizes the completion on the way back (`app/privacy_gateway/pipeline.py`).
  Messages are persisted as `sanitized_content` (pseudonymized), and
  `get_messages` deanonymizes on read for display.
- `LLMProvider` (ADR-0016) is the single choke point (ADR-0013) through which
  pseudonymized text reaches any external call. Two adapters ship today:
  `OpenAIProvider` (POST `/chat/completions`, already message-list shaped) and
  `OllamaProvider` (POST `/api/generate`, single-prompt shaped).
- `llm_requests` already records `provider`, `model`, `tokens_in`, `tokens_out`,
  `cost_usd`, `latency_ms` per request. OpenAI pricing is hardcoded in
  `openai_provider._PRICING_PER_1K_TOKENS`; Ollama cost is always 0.
- The provider is a global singleton built from env vars (`llm_provider`,
  `ollama_model`, `openai_model`) via `lru_cache`d `get_provider()`.
- RBAC (migration 0006): code-level `Permission` enum + sparse per-tenant
  `tenant_role_permissions` overrides; adding a permission needs no data backfill;
  `super_admin` bypasses (always every permission); `require_permission(...)`
  gates endpoints; the admin matrix UI shows `MATRIX_PERMISSIONS`.

## Design

### 1. Model catalog — `app/llm_gateway/catalog.py`

A code-defined source of truth shared by pricing, the usage view, context-window
trimming, and the model selector. Replaces the hardcoded pricing dict in
`openai_provider`.

```python
@dataclass(frozen=True)
class ModelEntry:
    key: str                 # "openai:gpt-4o", "ollama:llama3.1"
    provider: str            # "openai" | "ollama"
    model: str               # provider-native model id
    label: str               # human label for the selector
    context_window: int      # max tokens (prompt + completion)
    price_in_per_1k: Decimal
    price_out_per_1k: Decimal
    is_default: bool         # exactly one True

CATALOG: tuple[ModelEntry, ...] = (...)
```

- `get_entry(key) -> ModelEntry | None`, `default_entry() -> ModelEntry`,
  `list_entries() -> list[ModelEntry]`, `larger_models(key) -> list[ModelEntry]`
  (entries with `context_window > current`, sorted ascending by window, top 3 —
  the "propose switch" candidates).
- Context-window values are set to each provider's documented model max.
  **These must be confirmed against provider docs at implementation time** — the
  spec does not pin fabricated numbers. The existing configured models
  (`ollama:llama3.1`, `openai:gpt-5-nano`) plus at least one larger-window model
  (e.g. `openai:gpt-4o`) so the "propose switch" list is non-empty in the common
  case.
- Pricing for OpenAI models moves here from `openai_provider`; Ollama entries
  keep `0/0`. `OpenAIProvider` reads `price_in/out_per_1k` from its `ModelEntry`
  (passed by the registry) instead of the hardcoded dict. Unknown-but-listed
  models still cost 0 (the existing "a rename never blocks a chat" rule).

### 2. Provider interface + adapters

`LLMProvider.complete` changes shape:

```python
@dataclass(frozen=True)
class ChatMessage:
    role: str        # "system" | "user" | "assistant"
    content: str

class LLMProvider(Protocol):
    name: str
    model: str
    def complete(self, messages: list[ChatMessage]) -> LLMCompletion: ...
```

- The orchestrator builds the full message list **including** the
  `TOKEN_PRESERVATION_SYSTEM_PROMPT` as the first `system` message. Adapters pass
  the list through verbatim; they no longer prepend the system prompt themselves
  (removes a duplication that only existed because there was a single message).
  `SANITIZED_PROMPT_TEMPERATURE` stays applied by `OllamaProvider` only.
- `OpenAIProvider`: `messages` maps directly to the OpenAI `messages` field.
  Pricing computed from the `ModelEntry` it was constructed with.
- `OllamaProvider`: migrate `/api/generate` → `/api/chat`, which natively takes
  `messages: [{role, content}, ...]` + `options` (`temperature`, `num_ctx` set
  from the catalog `context_window`). Token counts still read from
  `prompt_eval_count`/`eval_count` best-effort; cost stays 0.
- `LLMCompletion` is unchanged (`text`, `tokens_in`, `tokens_out`, `cost_usd`).

### 3. Token estimator + context trimmer — `app/llm_gateway/context_window.py`

```python
_TOKENS_PER_CHAR = 1 / 4          # ~4 chars/token, conservative for European text
_PER_MESSAGE_OVERHEAD = 4         # approx role/wrapper overhead, per message

def estimate_tokens(messages: list[ChatMessage]) -> int: ...

def fit_to_window(
    messages: list[ChatMessage],
    context_window: int,
    reserve_output: int = 1024,
) -> tuple[list[ChatMessage], int]:
    """Drop oldest messages (after the leading system message) until the
    estimate fits in context_window - reserve_output. The system message is
    never dropped. Returns (fitted_messages, dropped_count)."""
```

- A conservative over-estimate is intentional: trimming slightly early is cheap;
  overrunning and eating the provider's output budget (or getting a 400) is not.
- `reserve_output` leaves room for the completion so a prompt that exactly fills
  the window still gets a non-empty reply.

### 4. Per-conversation model — migration 0007

Add `conversations.model_key` (`String`, nullable). Nullable so the migration is
backfill-free; the chat flow treats null as the catalog default (see §6). New
conversations are created with the default catalog key, so null is only ever seen
on pre-existing rows.

- `ConversationRepository.set_model(tenant_id, conversation_id, model_key)`.
- `ConversationDetail` / `ConversationSummary` schemas expose `model_key` and the
  resolved `model_label` (the orchestrator looks the label up in the catalog; the
  DB stores only the key).

### 5. Provider registry — per-model providers

`registry.py` gains:

```python
@lru_cache(maxsize=None)
def get_provider_for_model(model_key: str) -> LLMProvider: ...
```

Builds `OpenAIProvider` (api key + `ModelEntry`) or `OllamaProvider` (base_url +
`ModelEntry` + `num_ctx`) from settings + catalog. `get_provider()` is kept as a
thin convenience that resolves the default key (existing callers/tests still
work), but the chat flow uses `get_provider_for_model`.

### 6. Chat flow — `app/api/chat.py`

1. Load conversation; verify ownership/visibility (unchanged 404 semantics).
2. Resolve `model_key = conversation.model_key or catalog.default_entry().key`;
   `entry = catalog.get_entry(model_key)`; `provider = get_provider_for_model(key)`.
3. Load prior messages (`MessageRepository.list_for_conversation`) and build
   `messages = [system(TOKEN_PRESERVATION_SYSTEM_PROMPT), *history, user(sanitized_prompt)]`.
   History uses each stored message's `sanitized_content` and `role`
   (pseudonymized — same boundary as today).
4. `estimate_tokens(messages)`; if `> entry.context_window - reserve_output`:
   `(messages, dropped_count) = fit_to_window(...)`, set `trimmed = True`.
   Else `dropped_count = 0`, `trimmed = False`.
5. `provider.complete(messages)` → completion (unchanged 502 on `LLMProviderError`).
6. Deanonymize + persist assistant message + `llm_request` (unchanged), using
   the resolved `entry`/`provider`/`model`.
7. Extended SSE `done` event:
   ```json
   { "id": "...", "created_at": "...",
     "usage": { "tokens_in": N, "tokens_out": N, "cost_usd": "0.000123", "model": "openai:gpt-4o" },
     "context": { "trimmed": true, "dropped_count": 3, "window_size": 128000, "estimated_tokens": 127040 },
     "proposed_models": [ { "key": "openai:gpt-4o", "label": "GPT-4o", "context_window": 128000 }, ... ] }
   ```
   `proposed_models` is populated only when `trimmed`; otherwise `[]`.

### 7. New endpoints

- `GET /api/models` → `list[ModelOut]` (`key`, `label`, `provider`,
  `context_window`, `price_in_per_1k`, `price_out_per_1k`, `is_default`).
  Any authenticated user (the selector needs it).
- `PATCH /api/conversations/{id}` body `{ model_key: str }` → updates the
  conversation's model. Gated by a **new `MODELS_SWITCH` permission**
  (`models:switch`, see §8). Validates `model_key` against the catalog
  (422 on unknown key). Returns the updated `ConversationDetail`. Permission
  check runs first (403 if the user lacks `models:switch`); existence/visibility
  are then checked inside the handler (404 if the conversation doesn't exist or
  isn't visible). This means a user without the permission gets 403 even for a
  non-existent conversation — acceptable and consistent with how
  `require_permission` gates other capabilities (e.g. conversation creation),
  where the check is about the capability, not about a specific resource.
- `GET /api/conversations/{id}/usage` → aggregated `llm_requests` for the
  conversation: `{ request_count, tokens_in, tokens_out, cost_usd,
  per_model: [{ model, request_count, tokens_in, tokens_out, cost_usd }] }`.
  RBAC = `can_read_conversation` (same visibility as reading messages).

### 8. New RBAC permission

- Add `Permission.MODELS_SWITCH = "models:switch"` to the enum.
- Add it to `MATRIX_PERMISSIONS` so it appears in the admin matrix UI and is
  grantable per role per tenant.
- `DEFAULT_PERMISSIONS` does **not** include it for doctor/staff — switching is
  admin-granted by default (the user's choice). `super_admin` bypasses as always.
- `PATCH /api/conversations/{id}` uses `require_permission(Permission.MODELS_SWITCH)`
  (403 if lacking the capability), then the handler checks existence/visibility
  (404). The existing "404, not 403" rule applies to *read* access to a specific
  conversation and is unchanged; here the 403 is about the *capability* to switch
  models at all, which is the same shape as conversation-creation gating.

### 9. Frontend

- `lib/api/types.ts`: add `ModelOut`, `ConversationUsage`, extend `done` event
  payload (`usage`, `context`, `proposed_models`); add `model_key`/`model_label`
  to `ConversationDetail`.
- `lib/api/chat.ts`: parse the extended `done` event; add `getModels()`,
  `getConversationUsage(token, id)`, `patchConversationModel(token, id, key)`.
- Chat view (`app/(chat)/c/[conversationId]/page.tsx`):
  - Inline usage panel (tokens in/out, running cost, active model label) —
    seeded by `GET …/usage` and updated live from each `done.usage`.
  - Model selector dropdown populated from `GET /api/models`; selecting calls
    `patchConversationModel`. Shown only when the user has `models:switch`
    (exposed via `/api/me` permissions) and the conversation is not read-only;
    otherwise hidden. Hidden, not disabled, keeps the read-only view uncluttered
    and avoids surfacing a capability the user cannot act on.
  - Non-blocking notice when `done.context.trimmed`: "Context window reached —
    sent only the latest N messages. Switch to a larger model?" with
    `proposed_models` rendered as buttons; one click PATCHes the model and
    dismisses the notice.
- Read-only conversations: the model selector and composer are both hidden
  (existing `isReadOnly` behavior extended).

### 10. Testing

**Unit:**
- `catalog`: lookup, default, `larger_models` ordering/empty case.
- `context_window.estimate_tokens` and `fit_to_window`: boundary cases, system
  message never dropped, `reserve_output` honored, empty history.
- `registry.get_provider_for_model`: builds the right adapter per provider;
  unknown key raises.
- Both adapters with a multi-message list (OpenAI passes messages through; Ollama
  posts to `/api/chat` with `num_ctx`); pricing read from the catalog entry.

**Integration:**
- A multi-turn conversation: the second send includes the first turn in
  `messages` (assert via a captured provider request).
- History exceeds window → `done.context.trimmed` is true, `dropped_count > 0`,
  `proposed_models` non-empty, oldest messages absent from the provider request.
- Model switch via `PATCH` persists and the next send uses the new model.
- `GET …/usage` aggregates correctly across models; non-visible users get 404.
- `models:switch` permission gates the PATCH (denied → 403, granted → 200,
  `super_admin` always 200).

**Privacy invariants:**
- Only `sanitized_content` ever appears in the `messages` list sent to the
  provider; no raw PII leaks via history replay.
- Deanonymization and the output guard are unchanged and still pass their
  existing corpus tests.

## Migration / rollout

- Migration 0007 adds nullable `conversations.model_key` (no backfill).
- New permission `models:switch` needs no data backfill (sparse defaults).
- The provider interface change (`complete(prompt)` → `complete(messages)`) is a
  breaking change to the internal protocol; all call sites and tests are updated
  in the same change. No external API contract breaks except the additive `done`
  event fields and new endpoints.

## Risks

- **Ollama `/api/chat` behavior differs from `/api/generate`** (e.g. streaming
  defaults, `num_ctx` handling). Mitigation: integration test against a stubbed
  Ollama; confirm `num_ctx` is honored.
- **Heuristic token estimate is wrong for non-English / medical German.** Accepted:
  conservative over-trim is the safe direction; provider-reported counts remain
  authoritative for display.
- **Context-window values go stale** as providers change models. Mitigation: the
  catalog is code, reviewed at deploy; `larger_models` degrades gracefully (empty
  list → notice still shows, just no proposals).