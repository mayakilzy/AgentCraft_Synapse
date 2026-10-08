# ADR-0007 — G01 conditional acceptance (user-approved)

- **Status**: Accepted (user-conditional, recorded 2026-10-08)
- **Date**: 2026-10-08
- **Group**: G01 closure → G02 audit preparation

## Context

The user reviewed `reports/GROUP_01_REPORT.md` and the
`reports/GROUP_01_EVIDENCE.json` evidence file. The user's response
accepted G01 conditionally — i.e. G01 is approved as a foundation, but
is **not** production-certified, and three explicit decisions were made
governing the next group.

## Decision

G01 is **conditionally accepted** by the user. This is recorded as a
non-blocking milestone approval. The conditions and decisions are:

1. **Decision D-01 — Toolkit audit not waived.**
   `TOOLKIT_INDEX(1).json` is required as an inventory input and will be
   provided separately by the user. The agent must NOT fabricate the
   toolkit contents or claim any tool is "verified runnable" without
   inspecting the real inventory file. Until the file is provided, the
   G02 audit cannot start; only audit **preparation** (templates,
   validators, scope definition) is permitted.

2. **Decision D-02 — Production identity-provider selection deferred.**
   The auth abstraction (`AuthAdapter` protocol,
   `DevelopmentAuthAdapter`, `ProductionAuthAdapter`) and the external
   API compatibility surface (`/api/v1/*` routes, error envelope,
   OpenAPI 3.1 schema) must be preserved. The agent must NOT pick an
   OIDC/OAuth2 vendor or modify the auth contract structure. A future
   group, after the user's selection, will register the real provider.

3. **Decision D-03 — Licensing deferred.**
   No `LICENSE` file may be added without explicit user approval. The
   repo currently has no LICENSE file (as documented in
   `docs/baseline.md`); this remains the case until the user chooses.

## Next authorization scope

**G02 Audit Preparation ONLY.** The agent is authorized to:

- Prepare audit templates, schemas, validators, and the Layer-1
  Capability Matrix structure.
- Document the audit's required inputs, outputs, and acceptance gates.
- NOT inspect or claim any tool is verified-runnable until the
  `TOOLKIT_INDEX(1).json` file is present.

**G02 implementation is NOT authorized.** The agent must not write
acquisition, crawling, browser, or extraction code until both:

- (a) the user has provided `TOOLKIT_INDEX(1).json`, AND
- (b) the agent has submitted the audit report and provider selection, AND
- (c) the user has approved that audit and provider selection.

## Consequences

- ✅ Auth abstraction and API contract are frozen — future groups
  cannot silently change them.
- ✅ Audit traceability is preserved — no "verified" claims without
  real inspection.
- ⚠️ G02 cannot proceed past preparation until the user pushes the
  toolkit inventory.
- ⚠️ Any G01 code changes require a verified defect justification —
  no opportunistic refactors.
