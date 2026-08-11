# 0017 — Privacy/Utility Benchmark Methodology

**Status:** Accepted

## Context

This project is fundamentally an evaluation platform for comparing
anonymization strategies, not just a chat application. Without a defined,
repeatable benchmark, "does this detection approach work" has no answer
better than anecdote.

## Decision

A golden corpus of ~20–30 hand-crafted synthetic German clinical notes,
annotated with ground-truth entity spans/types and including deliberate
quasi-identifier combinations, drives an automated pytest-based benchmark
(`evaluation/`) measuring privacy (precision/recall/F1, leakage rate,
token-resolution failure rate), utility (embedding similarity between
pseudonymized-round-trip and raw-text-baseline answers, baseline via Ollama
only), performance (per-stage latency), and cost (tokens/$ per external
call, from `llm_requests`). Full detail in design doc §8.

## Alternatives Considered

- **No formal benchmark for MVP, evaluate qualitatively:** rejected —
  contradicts the project's explicit purpose as an evaluation platform;
  "define the first benchmark" was an explicit deliverable before MVP
  implementation begins.
- **Build a full multi-strategy (A–E) comparison harness now:** deferred to
  Phase 3, not rejected — MVP's single hybrid strategy needs its own
  baseline benchmark before a comparison across strategies is meaningful;
  building the comparison harness first would have nothing correct to
  compare against yet.
- **Real (anonymized public) datasets only, no hand-crafted corpus:**
  rejected as the sole source — hand-crafted cases are necessary to
  guarantee coverage of specific quasi-identifier combinations (the
  re-identification test cases, ADR-0015) that a found dataset can't be
  relied on to contain.

## Consequences

The golden corpus is small by ML-evaluation standards; this is an accepted
MVP trade-off given rule #1 (prefer simple components) — statistical power
for the benchmark itself is an explicit item to revisit once Phase 3 grows
the corpus alongside the multi-strategy comparison.

## Security Implications

The benchmark's leakage-rate metric is what makes ADR-0013's boundary claim
empirically checked, not just structurally enforced — both matter.

## Privacy Implications

Establishes measured baselines (not aspirational claims) for what this
system's detection actually achieves, which is required before any
utility/privacy trade-off claim can be made responsibly.

## Reversibility

High — the corpus and metrics are additive; Phase 3 extends rather than
replaces this methodology.
