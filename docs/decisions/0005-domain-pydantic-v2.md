# ADR-0005 — Pydantic v2 typed domain contracts

- **Status**: Accepted (binding per `DECISIONS_AND_ASSUMPTIONS.md`)
- **Date**: 2026-10-08
- **Group**: G01-T02

## Context

The Master Spec §5 lists 12 canonical domain entities (Source, Acquisition,
EvidenceFragment, Entity, Claim, Relationship, Capability, Hypothesis,
Experiment, EvidenceDelta, Scenario, Job) plus invariants governing their
state transitions. `DOMAIN_AND_API_CONTRACTS.md` enumerates the required
fields and the binding invariants (e.g. "every verified claim has at least
one evidence fragment").

These contracts must be:

1. **Typed** — fields, types, validators enforced at boundary
2. **Versioned** — every record carries an explicit `version` field
3. **Testable** — invariants verified by unit tests, not by hopes

## Decision

- Every domain record is a `pydantic.BaseModel` with `model_config =
  ConfigDict(extra="forbid", frozen=False, str_strip_whitespace=True)`.
- Common base `DomainRecord` provides `id: str` (opaque stable ID, default
  `uuid4().hex`), `created_at` / `updated_at` UTC timestamps, `version: int`
  starting at `1`.
- Enums are explicit `str`-backed `Enum`s, e.g. `EpistemicState`,
  `RelationshipOrigin`, `AcquisitionStatus`, `HorizonClass`, `JobStatus`.
- Invariants are enforced in `@model_validator(mode="after")`:
  - `Claim` with `epistemic_state == "supported"` MUST have
    `len(evidence_refs) >= 1` else `ValidationError`.
  - `Relationship` with `origin in {"derived", "hypothesized"}` MUST NOT
    have `verification_state == "verified"` (forbidden auto-promotion).
  - `Acquisition` with `completeness == "full"` MUST NOT have an
    `error_code` set.
  - `Job.status` transitions are validated against a state-machine dict.
- DTOs (response envelopes) are separate Pydantic models under
  `synapse.api.responses` — never serialize ORM models directly.

## Consequences

- ✅ Invalid records cannot be constructed → cannot be persisted
- ✅ Tests in `tests/unit/domain/` enforce every invariant as a regression
  guard
- ⚠️ Migration to a different runtime (e.g. dataclasses) would require
  rewriting all validators; Pydantic v2 is a deliberate dependency
