"""Storage layer — SQLAlchemy 2.0 async ORM, session, migrations.

This package is intentionally thin in G01. It contains:

- ``base`` — declarative base + naming convention
- ``db`` — async engine factory, session dependency, test isolation helper
- ``models`` — minimal ORM models for G01: ``sources``, ``jobs``,
  ``idempotency_keys``, ``audit_events``. Domain records (Claims,
  Relationships, …) get their own tables in later groups.
"""
