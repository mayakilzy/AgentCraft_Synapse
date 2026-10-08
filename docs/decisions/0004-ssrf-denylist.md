# ADR-0004 — SSRF denylist by default

- **Status**: Accepted (binding per Master Spec §17)
- **Date**: 2026-10-08
- **Group**: G01-T04

## Context

Master Spec §17 mandates protection against SSRF, unsafe URLs, malicious
files, prompt injection, and credential leakage. Synapse will eventually
fetch arbitrary web content (G02 Acquisition); the SSRF guard must exist
**before** any fetch code is written.

## Decision

`synapse.security.ssrf.validate_url(url, allow_private=False)` enforces:

1. **Scheme allowlist**: only `http` and `https`. Rejects `file://`,
   `ftp://`, `gopher://`, `data:`, `javascript:`, etc.
2. **Hostname resolution**: resolves the host to its IP address(es) and
   checks each against a denylist.
3. **Denylist** (when `allow_private=False`, the default):
   - `127.0.0.0/8`, `::1` (loopback)
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918 private)
   - `169.254.0.0/16` (link-local — includes AWS / GCP / Azure metadata
     `169.254.169.254`)
   - `0.0.0.0/8`, `100.64.0.0/10` (carrier-grade NAT)
   - `fc00::/7` (IPv6 unique-local)
   - `fe80::/10` (IPv6 link-local)
   - `::ffff:0:0/96` (IPv4-mapped IPv6, to prevent bypass)
4. **Per-fetch limits** (enforced in G02): `SYNAPSE_SSRF_FETCH_TIMEOUT_SECONDS`
   and `SYNAPSE_SSRF_MAX_RESPONSE_BYTES`.
5. The check runs on the **resolved IP**, not the hostname, so DNS-rebinding
   tricks that point a public hostname at a private IP are rejected.

In G01, the function is unit-tested but no HTTP fetch code calls it yet.
It will be wired into G02 Acquisition.

## Consequences

- ✅ The guard exists before any fetcher is committed
- ✅ Tested with positive & negative cases (loopback, RFC 1918, metadata IP,
  IPv6 variants, scheme tricks)
- ⚠️ DNS-rebinding attacks where a hostname flips between public and private
  addresses need TOCTOU checks at fetch time (added to G02 todo)
