# GROUP_02_SECURITY_DB_CLOSURE

## Header

| Field | Value |
|-------|-------|
| Closure date | 2026-10-08 |
| Operator | GLM main agent |
| Synapse repository | `https://github.com/mayakilzy/AgentCraft_Synapse.git` |
| Branch | `main` |
| Base SHA | `d6068d3` (HEAD of G02 final qualification) |
| Final SHA | `508f1d3`  |
| Authorization | User's "G02 Final Security & Database Closure" message |

## Overall STATUS: **PASS_WITH_LIMITATION**

Two of three closure items pass; PostgreSQL migration validation is
BLOCKED (no PostgreSQL available in this environment).

## Requirement 1: DNS Rebinding / TOCTOU — **PASS**

### Mitigation implemented: validated-IP connection pinning

**Approach**: `SSRFGuardedAsyncTransport` now calls
`validate_url_with_pin()` which resolves DNS, validates every returned
IP against the SSRF denylist, and returns the first safe IP as a
"pinned" IP. The transport then **rewrites the request URL** to use
the pinned IP as the host. When httpcore sees an IP literal as the
host, it connects directly — **no second DNS lookup**. This closes
the TOCTOU window: even if DNS changes between validation and
connection, the TCP connection goes to the originally-validated safe
IP.

**Not a custom networking stack**: the transport uses httpx's built-in
`AsyncHTTPTransport` for all HTTP/TLS I/O. Only the URL is rewritten
to pin the IP. TLS SNI and certificate verification use the original
hostname via `extensions["sni_hostname"]`. The `Host` header is set
to the original hostname. Redirect targets are re-validated and
re-pinned on each redirect.

**Key implementation** (`src/synapse/security/http_transport.py`):

```python
# validate_url_with_pin resolves DNS → returns (url, pinned_ip)
validated_url, pinned_ip = validate_url_with_pin(url, allow_private=...)

# Transport rewrites URL to use pinned IP
# httpcore skips DNS for IP literals — TCP connects to pinned_ip
pinned_url_str = f"{scheme}://{ip_host}:{port}{path}?{query}"
pinned_request = httpx.Request(
    method=..., url=pinned_url,
    headers={..., "host": original_hostname},
    extensions={..., "sni_hostname": original_hostname},
)
```

### Deterministic negative tests

| Test | What it simulates | Result |
|------|-------------------|--------|
| `test_dns_rebinding_attack_blocked_by_ip_pinning` | DNS returns public IP on 1st call, private IP on 2nd call. With pinning, 2nd call NEVER HAPPENS. | ✅ PASS — `getaddrinfo` called exactly once; pinned IP is the safe one |
| `test_dns_rebinding_url_is_rewritten_to_pinned_ip` | Verifies the request URL passed to the wrapped transport uses the pinned IP (not the hostname). Also verifies Host header and SNI are the original hostname. | ✅ PASS |
| `test_dns_rebinding_no_second_lookup_for_redirect_targets` | Two different hostnames → two DNS lookups (one per hostname); each connection uses the pinned IP (no additional lookup). | ✅ PASS |
| `test_validate_url_with_pin_returns_pinned_ip_for_hostname` | Basic: hostname → DNS → safe IP returned as pinned_ip. | ✅ PASS |
| `test_validate_url_with_pin_returns_none_for_ip_literal` | When host is already an IP, no DNS resolution — pinning returns None. | ✅ PASS |
| `test_validate_url_with_pin_blocks_private_ip_literal` | Private IP literal blocked immediately. | ✅ PASS |
| `test_validate_url_with_pin_blocks_loopback_ip_literal` | Loopback IP literal blocked. | ✅ PASS |
| `test_validate_url_with_pin_blocks_metadata_ip_literal` | Metadata IP literal blocked. | ✅ PASS |
| `test_validate_url_with_pin_blocks_hostname_resolving_to_private` | Hostname resolving to private IP blocked. | ✅ PASS |
| `test_format_ipv4_for_url` | IPv4 formatting for URL. | ✅ PASS |
| `test_format_ipv6_for_url` | IPv6 needs brackets in URL. | ✅ PASS |
| `test_redirect_to_metadata_ip_blocked_with_pinning` | Redirect to metadata IP blocked even with pinning. | ✅ PASS |
| `test_redirect_to_loopback_blocked_with_pinning` | Redirect to loopback blocked. | ✅ PASS |
| `test_redirect_to_rfc1918_blocked_with_pinning` | Redirect to RFC1918 blocked. | ✅ PASS |
| `test_redirect_to_file_scheme_blocked` | file:// blocked. | ✅ PASS |
| `test_safe_url_delegates_with_pinning` | Safe URL → transport pins IP + delegates with rewritten URL. | ✅ PASS |
| `test_safe_ip_literal_url_delegates_without_pinning` | Safe IP literal → no pinning needed, delegates directly. | ✅ PASS |

**Total**: 17 DNS-rebinding tests, all PASS.

## Requirement 2: PostgreSQL Migration Validation — **BLOCKED**

### Status

| Check | Result |
|-------|--------|
| PostgreSQL binary available? | ❌ No — `find / -name "postgres"` returns nothing |
| Docker available? | ❌ No — `which docker` returns nothing |
| `testing.postgresql` pip package? | ❌ No |
| `initdb` binary available? | ❌ No |
| Root/sudo access to install? | ❌ No — `apt-get install` fails with permission denied |
| pglast (PostgreSQL C parser) available? | ✅ Yes — `pglast v8.5` validates SQL syntax |

Per the user's instruction: *"If PostgreSQL is unavailable, report BLOCKED rather than PASS."*

**PostgreSQL migration validation is BLOCKED.**

### What was verified instead

| Check | Method | Result |
|-------|--------|--------|
| Migration SQL is valid PostgreSQL syntax | pglast parser (real PG C parser via libpg_query) | ✅ PASS — `0001_initial` upgrade, `0002_evidence_fragments` upgrade + downgrade all parse as valid PG |
| Migration 0001 unchanged from G01 | `git diff 7a9088a HEAD -- alembic/versions/0001_initial.py` | ✅ PASS — empty diff |
| Migration 0002 is additive | AST analysis of revision/down_revision + no `op.alter_column` | ✅ PASS |
| SQLite upgrade/downgrade/round-trip | Real Alembic on SQLite | ✅ PASS — all 6 migration tests pass |

### What was NOT verified

- Real PostgreSQL CREATE TABLE behavior (constraint enforcement, index creation, type compatibility)
- Real PostgreSQL downgrade (DROP TABLE, DROP INDEX on PG)
- Real PostgreSQL round-trip on a live PG instance

### Required action for the user

The user should run the following against a real disposable PostgreSQL
instance to fully verify:

```bash
# 1. Provision a disposable PG instance
docker run --rm -d --name synapse-pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=synapse -p 5433:5432 postgres:16

# 2. Run migrations
SYNAPSE_ENV=test \
SYNAPSE_DB_URL="postgresql+psycopg://postgres:test@localhost:5433/synapse" \
SYNAPSE_AUTH_MODE=development \
SYNAPSE_CORS_ORIGINS="http://localhost:3000" \
SYNAPSE_CORS_ALLOW_CREDENTIALS=true \
python -m alembic upgrade head

# 3. Verify schema
psql -h localhost -p 5433 -U postgres -d synapse -c "\dt"
psql -h localhost -p 5433 -U postgres -d synapse -c "\d evidence_fragments"

# 4. Downgrade
SYNAPSE_ENV=test \
SYNAPSE_DB_URL="postgresql+psycopg://postgres:test@localhost:5433/synapse" \
SYNAPSE_AUTH_MODE=development \
SYNAPSE_CORS_ORIGINS="http://localhost:3000" \
SYNAPSE_CORS_ALLOW_CREDENTIALS=true \
python -m alembic downgrade base

# 5. Clean up
docker stop synapse-pg-test
```

## Requirement 3: Regression and Scope — **PASS**

### Lint + format

| Check | Result |
|-------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 92 files already formatted |

### Deterministic tests

| Suite | Collected | Passed | Failed | Skipped |
|-------|-----------|--------|--------|---------|
| `tests/unit/domain/` | 71 | 71 | 0 | 0 |
| `tests/unit/security/` (G01 SSRF + G02 redirect + DNS rebinding) | 55 | 55 | 0 | 0 |
| `tests/unit/api/` | 34 | 34 | 0 | 0 |
| `tests/unit/providers/` | 29 | 27 | 0 | 2 (live) |
| `tests/integration/` | 54 | 50 | 0 | 1 (live) + 3 new DNS-rebinding tests counted in security above |
| **TOTAL** | **243** | **237** | **0** | **3 (live)** |

### Live integration tests

| Test | Result |
|------|--------|
| `test_arxiv_live_discovery_returns_results` | ✅ PASS |
| `test_arxiv_live_discovery_with_author_filter` | ✅ PASS |
| `test_live_arxiv_discovery_and_ingest` | ✅ PASS — full e2e with IP-pinned SSRF transport |

### Scope compliance

| Constraint | Status |
|------------|--------|
| No new providers added | ✅ |
| No G02 functionality expanded | ✅ |
| No GROUP_03 implementation started | ✅ |
| No AgentCraft-Toolkit access or modification | ✅ |
| No write-capable credential used | ✅ |

## Toolkit protection

| Check | Result |
|-------|--------|
| Token file last-modified time | Unchanged from session start |
| Toolkit HEAD SHA | `fd9df34c51781bd12effab62762022ab04dbd771` (unchanged) |
| Toolkit working tree | clean |
| Toolkit push | correctly blocked |
| GitHub API write calls | 0 |

## Files changed in this closure

### Files modified (3)

| Path | Change |
|------|--------|
| `src/synapse/security/http_transport.py` | Replaced non-pinning transport with IP-pinning transport: added `validate_url_with_pin()`, URL rewriting with SNI override, Host header preservation, safe content handling for streaming requests |
| `tests/unit/security/test_ssrf_redirect.py` | Updated 1 test to work with IP pinning (patch `validate_url_with_pin` instead of `validate_url`; assert rewritten URL) |
| `tests/unit/security/test_dns_rebinding.py` | New: 17 DNS-rebinding tests |

### No files under `src/synapse/providers/`, `src/synapse/api/`, or `src/synapse/application/` were modified.

## Unresolved risks

| # | Risk | Severity | Status |
|---|------|----------|--------|
| R-01 | PostgreSQL migration validation not completed | Medium | **BLOCKED** — no PG available; user must verify against real PG |
| R-02 | TLS certificate verification with IP-pinned URL | Low | Mitigated: `extensions["sni_hostname"]` ensures httpcore verifies the cert against the original hostname, not the IP |
| R-03 | Connection pool may reuse a connection to a different IP for the same hostname | Low | Acceptable: httpx's connection pool keys by (scheme, host, port). Since the pinned URL has a different host (the IP), the pool creates a new connection per pinned IP. This is correct behavior — it prevents connection reuse across different IPs for the same hostname. |
| R-04 | The credential is write-capable (per ADR-0009 §7) | Medium | Not used in this closure; documented in `CORRECTED_CREDENTIAL_STATEMENT.md` |

## Final commit SHA

| Field | Value |
|-------|-------|
| Branch | `main` |
| Base SHA | `d6068d3` |
| Final SHA | `508f1d3`  |
| Pushed to `origin/main` | yes  |

## STOP statement

**This closure is complete. The agent will NOT:**

- Add any new providers.
- Begin GROUP_03.
- Modify the Toolkit repository.
- Use the write-capable credential.

The agent awaits explicit user approval before any further work.

Per the user's "G02 Final Security & Database Closure" message:
> *"STOP for approval."*

---

*End of GROUP_02 Security & DB Closure — STOP for approval.*
