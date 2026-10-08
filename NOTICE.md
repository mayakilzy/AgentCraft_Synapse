# NOTICE — Third-Party Attribution

This file gives attribution to third-party components used by
AgentCraft Synapse, per the license terms of each component.

## Bundled dependencies (via pyproject.toml)

| Package | Version | License | Source |
|---------|---------|---------|--------|
| FastAPI | >=0.110,<1.0 | MIT | https://github.com/tiangolo/fastapi |
| Uvicorn | >=0.27,<1.0 | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic | >=2.6,<3.0 | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | >=2.2,<3.0 | MIT | https://github.com/pydantic/pydantic-settings |
| SQLAlchemy | >=2.0,<3.0 | MIT | https://github.com/sqlalchemy/sqlalchemy |
| Alembic | >=1.13,<2.0 | MIT | https://github.com/sqlalchemy/alembic |
| httpx | >=0.27,<1.0 | BSD-3-Clause | https://github.com/encode/httpx |
| anyio | >=4.2,<5.0 | MIT | https://github.com/agronholm/anyio |
| python-multipart | >=0.0.9,<1.0 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| trafilatura | >=2.3,<3.0 | Apache-2.0 | https://github.com/adbar/trafilatura |
| aiosqlite | >=0.20,<1.0 | MIT | https://github.com/omnilib/aiosqlite |
| psycopg | >=3.1,<4.0 | LGPL-2.1+ | https://github.com/psycopg/psycopg |
| jsonschema | >=4.20,<5.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| pytest | >=8.0,<10.0 | MIT | https://github.com/pytest-dev/pytest |
| ruff | >=0.4,<1.0 | MIT | https://github.com/astral-sh/ruff |

## Vendored components (copied into src/synapse/providers/)

Per ADR-0009 §4: the following files contain code vendored from the
AgentCraft-Toolkit repository at commit
`fd9df34c51781bd12effab62762022ab04dbd771` (2026-09-01). The Toolkit
itself is **not** a runtime dependency — these are static copies made
during the G02 audit and preserved unchanged.

### `src/synapse/providers/arxiv_search_provider.py`

- **Origin**: `ToolKit/research/arxiv_search_tool/tool.py`
- **Upstream source**: https://github.com/NousResearch/hermes-agent
- **License**: MIT
- **Copyright**: (c) 2025 Nous Research
- **Adapted**: yes — wrapped in the Synapse Provider protocol; the
  ArxivSearchTool class itself is preserved verbatim.

### `src/synapse/providers/content_delta_hash.py`

- **Origin**: `ToolKit/library/agentcraft_toolkit/scraping/delta_hash.py`
- **Upstream source**: https://github.com/mayakilzy/AgentCraft-Toolkit
- **License**: MIT
- **Copyright**: (c) AgentCraft Team
- **Adapted**: yes — the `fingerprint()` function and its `_normalize()`
  helper are preserved; the surrounding library is not vendored.

### `src/synapse/providers/trafilatura_extractor_provider.py`

- **Origin**: NOT vendored — this is a thin Synapse wrapper around the
  pip-installed `trafilatura` package (Apache-2.0). The ToolKit's
  adapter at `ToolKit/web/trafilatura_extractor_tool/tool.py` informed
  the design but was not copied.
- **License**: Apache-2.0 (the wrapper itself is MIT-equivalent Synapse code)

## License of this repository

Proprietary — internal use only. See `README.md` for the project's
license stance. The repository currently has no `LICENSE` file (per
ADR-0007 decision D-03 — licensing is deferred until explicit user
approval).

## Compliance notes

- All GPL/AGPL/LGPL/SSPL components are flagged here even when the
  license is permissive in practice (psycopg's LGPL-2.1+ applies to
  the library itself, not to code that merely imports it).
- The vendored `arxiv_search_provider.py` and `content_delta_hash.py`
  files start with provenance headers naming the upstream repo,
  license, and Toolkit commit SHA — see each file's docstring.
- No component was modified from its upstream form except as documented
  in the "Adapted" notes above.

---

*This NOTICE file is required by the MIT and Apache-2.0 licenses of
the vendored components. Update this file whenever a new dependency
or vendored component is added.*
