# Upstream Repair Requests — AgentCraft-Toolkit

> Per ADR-0009 §8: the Synapse agent never modifies, repairs, or pushes
> to the AgentCraft-Toolkit repository or any of its subordinate source
> repositories. Any required upstream repair is documented here and
> submitted to the authorized Toolkit agent under Master Agent
> governance.
>
> **Status**: Empty — no upstream repairs required as of 2026-10-08.

## How to use this file

When the Synapse implementation discovers a defect, missing feature,
or required upstream change in the Toolkit:

1. Add a new entry below (do not edit existing entries — append-only).
2. Mark the entry `STATUS: REQUESTED`.
3. Submit the entry to the authorized Toolkit agent (e.g., via the
   Master Agent governance channel — TBD by the user).
4. The Toolkit agent reviews, accepts/rejects, and updates the entry.
5. The Synapse implementation works around the gap in the meantime
   (per ADR-0009 §8) by writing a Synapse-side adapter that
   compensates, with a code comment pointing to the request ID here.

## Entry template

```
### UR-<NNN>: <short title>

- **Date requested**: YYYY-MM-DD
- **Requested by**: Synapse G02 implementation
- **Status**: REQUESTED | ACCEPTED | REJECTED | RESOLVED
- **Toolkit commit**: fd9df34c51781bd12effab62762022ab04dbd771
- **Affected file**: <ToolKit path>
- **Synapse workaround**: <description of the Synapse-side compensation>
- **Description**:
  <What is wrong, what is missing, or what would be improved. Include
  the exact reproduction: file path, function name, input, expected
  output, actual output.>
- **Requested change**:
  <Specific diff or description of the change the Toolkit agent should
  make.>
- **Justification**:
  <Why this matters to Synapse. What capability does it enable or
  what defect does it fix?>
- **Priority**: HIGH | MEDIUM | LOW
```

## Entries

_None yet._

---

*This file is append-only. The Synapse agent never edits past entries
once submitted.*
