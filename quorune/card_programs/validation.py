from __future__ import annotations

from dataclasses import asdict
import hashlib
from typing import TYPE_CHECKING

from ..util import stable_json

if TYPE_CHECKING:
    from ..carddb import CardDatabase
    from ..semantics import SemanticProgram, SemanticRegistry


def canonical_program_fingerprint(
    registry: "SemanticRegistry",
    program: "SemanticProgram",
) -> str | None:
    """Return the enclosing fingerprint only for an unchanged indexed ability."""

    if not program.oracle_id:
        return None
    card_program = registry.card_program_for_oracle(program.oracle_id)
    if card_program is None:
        return None
    canonical = next(
        (
            ability
            for ability in card_program.abilities
            if ability.key == program.key
        ),
        None,
    )
    if canonical is None or canonical.to_dict() != program.to_dict():
        return None
    return card_program.fingerprint


def program_source_is_current(
    card_db: "CardDatabase",
    program: "SemanticProgram",
) -> bool:
    from .admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, program_has_declared_complete_source_admission
    if (program.provenance.get(REQUIRES_COMPLETE_CARD_PROGRAM_FIELD) is True
            and not program_has_declared_complete_source_admission(program)):
        return False
    if not program.oracle_id:
        return False
    try:
        record = card_db.by_oracle_id(program.oracle_id)
    except KeyError:
        return False
    oracle_hash = hashlib.sha256(
        record.oracle_text.encode("utf-8")
    ).hexdigest()
    rulings_hash = hashlib.sha256(
        stable_json(
            sorted(
                (asdict(ruling) for ruling in card_db.rulings(record)),
                key=lambda row: (
                    str(row["published_at"]),
                    str(row["source"]),
                    str(row["comment"]),
                    str(row["oracle_id"]),
                ),
            )
        ).encode("utf-8")
    ).hexdigest()
    return (
        program.provenance.get("source_oracle_hash") == oracle_hash
        and program.provenance.get("source_rulings_hash") == rulings_hash
    )
