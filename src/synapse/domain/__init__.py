"""Synapse domain layer.

Typed Pydantic v2 records for the 12 canonical entities defined in the
Master Spec §5 and `DOMAIN_AND_API_CONTRACTS.md`. These are the **binding
contracts**; ORM models and API DTOs derive from them.

Each module imports its own records; this package only exposes the base
class and the shared enums.
"""

from synapse.domain._base import (
    AcquisitionCompleteness,
    AcquisitionStatus,
    DomainRecord,
    EntityType,
    EpistemicState,
    HorizonClass,
    JobStatus,
    Origin,
    RelationshipDirection,
    VerificationState,
)

__all__ = [
    "AcquisitionCompleteness",
    "AcquisitionStatus",
    "DomainRecord",
    "EntityType",
    "EpistemicState",
    "HorizonClass",
    "JobStatus",
    "Origin",
    "RelationshipDirection",
    "VerificationState",
]
