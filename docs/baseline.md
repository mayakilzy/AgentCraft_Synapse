# AgentCraft Synapse — Repository Baseline (G01-T01)

> Captured read-only reconnaissance of the repository before any G01 work.
> This file is generated and committed once at the start of G01-T01.
>
> **Update 2026-10-08 (post-G01 conditional acceptance):** G01 was
> conditionally accepted by the user (see ADR-0007). Decisions D-01,
> D-02, D-03 are recorded there. This baseline file remains accurate as
> a historical record of the pre-G01 repository state.

## 1. Repository identity

| Field | Value |
|-------|-------|
| Origin | `https://github.com/mayakilzy/AgentCraft_Synapse.git` |
| Initial state on clone | **Empty repository** (no commits, no tracked files) |
| Default branch | `main` |
| Initial commit SHA | (none — first commit pending) |
| Initial file count (tracked) | **0** |
| Initial source-language files | **0** |
| Initial test count | **0** |

The repository was created on GitHub and cloned with the user-supplied
developer token, but contained no commits, no source code, no CI, no tests,
no configuration, and no `.gitignore`. **No uncommitted work was overwritten**
because there was nothing to overwrite.

## 2. Toolkit location audit (per START_HERE_GLM §1)

`START_HERE_GLM.md` notes that `TOOLKIT_INDEX(1).json` was reviewed in the
planning conversation but **is not bundled** with the development package.
The instruction is explicit: *"locate the real toolkit in the workspace and
verify it; if missing, report BLOCKED instead of claiming a successful audit."*

### Audit steps performed

1. `ls -la` on the empty repository root → no files.
2. Search for `TOOLKIT_INDEX*` anywhere in the cloned `app_repo` → not present.
3. Search for `TOOLKIT_INDEX*` in the development package
   (`AgentCraft_Synapse_GLM_Development_Package_v1.0.zip` extracted under
   `workspace/`) → not present.
4. Search for `TOOLKIT_INDEX*` in the public GitHub clone of
   `mayakilzy/repo-info` (which contained `AgentCraft_Synapse/`,
   `BookForge/`, `Genesis/`, `Restaurant/`, `Rehabilitation-Agent/`,
   `Voxa Studio/`) → **not present**.
5. `pip show` for any of the candidate tools named in the Master Spec
   (`crawl4ai`, `scrapy`, `playwright`, `browser-use`, `trafilatura`,
   `extruct`, `feedparser`, `twikit`, `whoogle`) → none installed; this is
   a Python 3.12 sandbox with only FastAPI / Pydantic / SQLAlchemy / pytest /
   ruff / httpx available.

### Status: BLOCKED — for G02+, not for G01

The toolkit is genuinely absent from the workspace. Per START_HERE §6,
"presence in an index is not proof of runtime fitness" — and there is no
index at all here. This is **expected and acceptable for G01**, whose job is
to lay the foundation (domain contracts, persistence, API skeleton, security
baseline). No toolkit integration is required in G01; that begins in G02
(Acquisition).

**The decision is logged as a known limitation in the G01 report and an
open `decisions_required` item for the user**: the user must either (a) push
`TOOLKIT_INDEX(1).json` to the repo before approving G02, or (b) explicitly
authorize deferring toolkit integration to a later group.

## 3. Environment reconnaissance

| Item | Value |
|------|-------|
| Python | 3.12.14 (`/home/z/.venv/bin/python3`) |
| FastAPI | 0.128.0 |
| Pydantic | 2.12.5 |
| SQLAlchemy | 2.1.4 |
| Alembic | 1.20.0 |
| pytest | 9.0.2 |
| ruff | 0.16.10 |
| httpx | 0.28.1 |
| uvicorn | 0.44.0 |
| pydantic-settings | installed |
| PostgreSQL (`psql`) | **not installed** |
| SQLite | available via `aiosqlite` (added as dev dep) |

Because PostgreSQL is not installed in this sandbox, **tests use SQLite
in-memory** by default. The same migrations target Postgres in production.
This is a documented reversible default (see ADR-0002).

## 4. License & constraints

- The repository has no `LICENSE` file yet. Treated as **proprietary**
  pending the user's explicit license decision.
- The development package declares "Proprietary — internal use only".
- No third-party code with conflicting licenses was integrated in G01.

## 5. Reproducible commands (run before any source mutation)

```bash
git clone https://github.com/mayakilzy/AgentCraft_Synapse.git
cd AgentCraft_Synapse
git log --oneline        # → fatal: your branch 'main' does not have any commits yet
git status               # → nothing to commit, working tree clean
ls -la                   # → only .git/
find . -type f -not -path './.git/*'   # → (empty)
```

All of the above ran cleanly on inspection and **no source was mutated**.

## 6. Risks identified at baseline

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R-01 | Empty repo, no CI guardrails | Medium | G01-T05 sets up `.github/workflows/ci.yml` |
| R-02 | Toolkit index absent | High for G02+ | Deferred to user authorization before G02 |
| R-03 | No Postgres in dev sandbox | Low | SQLite test dialect (ADR-0002) |
| R-04 | No production secrets | Required | Sample `.env.example` only; `.env` git-ignored |
| R-05 | No license file | Medium | Logged as `decisions_required` |

## 7. Architectural baseline (Master Spec v2.0 confirmed)

The Master Architecture v2.0 (DOCX) was read in full. Key binding principles
recorded as ADRs:

- Modular monolith, single backend, single relational store (ADR-0001).
- API-first; internal & external clients share `/api/v1` (ADR-0006).
- Pydantic v2 typed domain contracts (ADR-0005).
- Local dev auth adapter, fail-closed production (ADR-0003).
- SSRF denylist by default (ADR-0004).
- pgvector / graph database deferred until measured workload requires it
  (binding decision §1 of `DECISIONS_AND_ASSUMPTIONS.md`).

The full set of decisions is in `docs/decisions/`.
