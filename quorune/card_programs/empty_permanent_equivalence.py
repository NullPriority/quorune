from __future__ import annotations

"""Replace only empty reviewed casts with complete native permanent rules."""

import hashlib
from typing import Iterable
from ..carddb import CardRecord
from ..rules.capabilities import CapabilityRegistry
from ..semantics import SemanticProgram
from .admission import CompleteCardProgramAdmission


def shadowed_reviewed_empty_permanent_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    oracle_ir=None,
) -> set[str]:
    if (capability_registry is None or record.layout!='normal' or record.faces
            or record.is_land or not record.is_permanent_spell):return set()
    source_views=tuple(p for p in reviewed_programs if p.oracle_id==record.oracle_id)
    reviewed=[p for p in source_views
        if p.trust_level=='trusted'
        and not str(p.provenance.get('authored_by','')).startswith('oracle-ir-v')
        and p.ability_id=='spell:front' and p.active_zone=='stack' and p.event=='resolve'
        and p.destination=='battlefield' and not p.requires_arbiter
        and not p.effects and not p.handlers and p.target_schema is None
        and p.cost_schema is None and p.event_condition is None and p.trigger_limit is None]
    generated=tuple(generated_programs)
    if not reviewed or not generated:return set()
    if oracle_ir is None:
        from ..oracle_ir import compile_oracle_card
        oracle_ir=compile_oracle_card(record,capability_registry=capability_registry,capability_profile=capability_profile)
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    if (oracle_ir.oracle_id!=record.oracle_id or oracle_ir.oracle_hash!=oracle_hash
            or not CompleteCardProgramAdmission.from_oracle_ir(oracle_ir).admitted):return set()
    from .binding import bind_semantic_program_runtime
    ruling_hashes=set()
    for current in generated:
        if (current.oracle_id!=record.oracle_id or current.trust_level!='trusted'
                or current.requires_arbiter
                or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or current.provenance.get('source_oracle_hash')!=oracle_hash
                or not current.provenance.get('source_rulings_hash')):return set()
        if not bind_semantic_program_runtime(current,capability_registry=capability_registry,
                profile=capability_profile)['strict']:return set()
        ruling_hashes.add(current.provenance['source_rulings_hash'])
    if len(ruling_hashes)!=1:return set()
    if any(p.provenance.get('source_oracle_hash')!=oracle_hash
           or p.provenance.get('source_rulings_hash') not in ruling_hashes
           for p in source_views):return set()
    return {p.key for p in reviewed if p.provenance.get('source_oracle_hash')==oracle_hash
        and p.provenance.get('source_rulings_hash') in ruling_hashes}
