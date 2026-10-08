# Corrected Credential Statement — G02 Audit

> **Status**: Correction issued 2026-10-08 per ADR-0009 §7.
>
> This document amends the credential claims made in
> `reports/GROUP_02_AUDIT_REPORT.md` and `reports/GROUP_02_AUDIT_EVIDENCE.json`.
> The original wording over-stated the read-only enforcement.

## Honest assessment

The Synapse developer token used to clone the AgentCraft-Toolkit
repository is **write-capable at the credential level**.

### Verified token scopes (2026-10-08)

```
$ curl -sI -H "Authorization: token $ACS_GIT_TOKEN" https://api.github.com/user
x-oauth-scopes: repo, workflow
```

The `repo` scope grants **read + write** to all repositories the
token's owner can access (public, private, organization, etc.).
The `workflow` scope grants the ability to modify GitHub Actions
workflow files. **This is a write-capable credential.**

## What the audit actually did (process-level mitigation only)

The audit's local clone was hardened against accidental writes:

| Mitigation | What it prevents | What it does NOT prevent |
|------------|------------------|--------------------------|
| `git remote set-url origin https://...AgentCraft-Toolkit.git` (token stripped) | The token being read from the local `.git/config` | The token being used directly via `curl` / GitHub API |
| `git remote set-url --push origin DISABLED-PUSH-BY-AUDIT-POLICY` | `git push` from this local clone succeeding | The token being used to push via a fresh clone elsewhere |
| `git config credential.helper ""` | Git auto-filling credentials from a system keychain | The token being used by any other tool that has it |
| Audit scripts never invoke write-capable GitHub API endpoints | Write API calls from the audit scripts | Write API calls from a future agent that re-reads the token |

**The credential itself remains write-capable.** Any agent (or human)
with access to `/home/z/my-project/.secure/agentcraft_token.env` can
still push to the user's repositories.

## Required future access posture

Per ADR-0009 §7, **future Toolkit audits MUST use one of the
following**:

### Option A — Repository-scoped fine-grained PAT (preferred)

- **Type**: Fine-grained personal access token
- **Repository access**: Selected repositories only — `AgentCraft-Toolkit`
  (read access), `AgentCraft_Synapse` (read+write if needed)
- **Permissions**: `Contents: Read` only (no `Write`, no `Administration`,
  no `Workflows`, no `Metadata` beyond what read requires)
- **Expiration**: Short (7 days or less)
- **Stored**: chmod-600 file outside the repo, deleted on session end

This credential cannot push even if the local git config is
compromised.

### Option B — Independently enforced read-only boundary

- A separate readonly mirror of the Toolkit repo is maintained (e.g.,
  via GitHub's "fork" with admin rights stripped, or via a server-side
  mirror that is updated by a scheduled pull-only job).
- The Synapse audit agent has read access to the mirror only.
- The original Toolkit repo is never on the audit agent's credential
  scope.

This requires infrastructure setup; preferred only if Option A is
infeasible.

### Option C — Anonymous public clone (when repo is public)

- If `AgentCraft-Toolkit` is made public, the audit can use
  unauthenticated HTTPS clone (`git clone
  https://github.com/mayakilzy/AgentCraft-Toolkit.git`) with zero
  credentials.
- This is the simplest and safest option, but requires the user to
  set the repo's visibility to public.

## What this correction changes

### In `reports/GROUP_02_AUDIT_REPORT.md`

The original wording under "Repository access and protection":

> *"Per mission §3, the agent enforced technical isolation..."*
> *"Verified push is blocked: `git push --dry-run` → exit 128"*
> *"The credential is read-only"*

is corrected to:

> *"The agent applied process-level mitigations on the local clone.
> The credential is write-capable (repo + workflow scopes); the local
> push URL was set to a non-existent value to prevent accidental
> pushes from this clone, but this does not change the credential's
> capabilities."*

### In `reports/GROUP_02_AUDIT_EVIDENCE.json`

The original field:

```json
"audit_authorization": {
  "read_only_enforcement": "technical isolation (pushurl disabled, credential.helper empty)",
  ...
}
```

is corrected to:

```json
"audit_authorization": {
  "credential_scope": "write-capable (repo + workflow OAuth scopes)",
  "process_level_mitigations": [
    "remote.origin.url stripped of token",
    "remote.origin.pushurl = DISABLED-PUSH-BY-AUDIT-POLICY",
    "credential.helper = ''"
  ],
  "credential_level_read_only": false,
  "residual_risk": "future audits with this token still carry write-risk at the credential level; mitigated only by process discipline",
  "required_future_action": "provision a repository-scoped fine-grained PAT with Contents:Read only, OR an independently enforced read-only boundary (per ADR-0009 §7)"
}
```

## What this correction does NOT change

- The audit results themselves (32 tools shortlisted, 2 smoke tests
  passed, etc.) are unchanged. The audit evidence is still valid
  as an inventory and discovery audit.
- The Toolkit commit SHA reference (`fd9df34`) is unchanged.
- The provider selection proposal is unchanged (subject to the
  minimal-execution refinement in
  `G02_MINIMAL_EXECUTION_PROPOSAL.md`).
- No file was actually pushed to the Toolkit repo during the audit
  (verified: the local clone's push URL was disabled; no GitHub API
  write calls were made).

## Audit trail

- 2026-10-08: Original audit (`GROUP_02_AUDIT_REPORT.md`) committed
  at Synapse SHA `13a2014`.
- 2026-10-08: Correction issued at this document and at
  `G02_MINIMAL_EXECUTION_PROPOSAL.md` (per ADR-0009 §7).
- Future audits: must use Option A, B, or C above. The current
  write-capable token must NOT be used for future Toolkit audits
  unless the user explicitly accepts the residual write-risk.

---

*This correction is binding. Future audits that use the same
write-capable token without explicit user acceptance of residual
risk are non-compliant with ADR-0009 §7.*
