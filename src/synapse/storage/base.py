"""Declarative base + naming convention for all ORM models.

The naming convention keeps generated constraint names stable across
re-runs, which makes Alembic autogenerate diffs much smaller.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable, conventional naming — Alembic relies on this to detect changes.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """All ORM models inherit from this."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
