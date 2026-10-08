# Provider Selection Proposal — TEMPLATE

> Filled in by the agent after the audit. **This file is a placeholder
> until `TOOLKIT_INDEX(1).json` is received and verified.**

## Goal

Propose the minimal set of provider adapters to wire into Synapse for
G02 implementation, following Master Spec §6:

> *"Audit deliverable fields: capability, required behavior, candidate
> tool, evidence of runtime operation, supported sources, metadata
> fidelity, relationship potential, speed, cost, failure modes, fallback,
> licensing, decision [adopt|adapt|defer|reject|gap]."*

## Selection principles

1. **Minimal reuse** — adopt one tool per capability domain where
   possible; avoid duplicate wrappers.
2. **No vendor lock-in** — provider adapters wrap tools behind the
   `Provider` protocol; tools can be swapped later.
3. **License-clean** — no GPL/AGPL tools linked into proprietary code
   without legal review (per ADR-0007 D-03 deferral).
4. **Compliant** — no scrapers that violate website ToS or robots.txt.
   X/Twitter ingestion uses only official compliant APIs (per
   `DECISIONS_AND_ASSUMPTIONS.md`).
5. **Bounded cost** — every external call has a configured cost cap
   (per Master Spec §14).

## Proposed providers

| Provider name | Capability domain | Tool | License | Verified runnable? | Default enabled? | Cost cap | Notes |
|---------------|-------------------|------|---------|--------------------|------------------|---------|-------|
| _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

## Rejected candidates

| Tool | Reason |
|------|--------|
| _pending_ | _pending_ |

## Deferred candidates

| Tool | Reason | Revisit when |
|------|--------|--------------|
| _pending_ | _pending_ | _pending_ |

## Required user decisions before implementation

_pending_

## STOP statement

**This proposal is part of G02 Audit Preparation.** The agent will NOT
register any provider adapter or write any provider implementation code
until the user explicitly approves this proposal.
