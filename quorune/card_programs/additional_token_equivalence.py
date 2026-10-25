from __future__ import annotations

"""Retire only source-identical compatibility views of fixed token replacements."""

from typing import Iterable
from ..carddb import CardRecord
from ..semantics import SemanticProgram
from ..semantic_runtime.token_replacements import GenericAdditionalTokenReplacementHandler


def shadowed_reviewed_additional_token_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    if record.faces or record.layout!='normal' or record.is_land:return set()
    generated=tuple(generated_programs);reviewed=tuple(reviewed_programs)
    shadowed=set();matched_sources=set()
    handler=GenericAdditionalTokenReplacementHandler()
    fields=('semantic_schema_version','active_zone','event','destination','effects','handlers',
            'target_schema','cost_schema','event_condition','trigger_limit','requires_arbiter')
    for current in generated:
        if (current.oracle_id!=record.oracle_id or current.trust_level!='trusted'
                or not current.capability_closure or current.capability_closure.get('trusted') is not True
                or current.capability_closure.get('blockers')!=[]
                or 'token.creation.additional_replacement' not in current.capability_dependencies
                or current.active_zone!='battlefield' or current.event!='token.create'
                or current.requires_arbiter or current.effects or current.target_schema is not None
                or current.cost_schema is not None or current.event_condition is not None
                or current.trigger_limit is not None or len(current.handlers)!=1
                or current.handlers[0].get('handler_id')!=handler.handler_id):
            continue
        handler.validate(current.handlers[0])
        for old in reviewed:
            if (old.oracle_id!=record.oracle_id or old.trust_level!='trusted'
                    or str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    or any(getattr(old,field)!=getattr(current,field) for field in fields)):
                continue
            if all(current.provenance.get(key)
                   and current.provenance[key]==old.provenance.get(key)
                   for key in ('source_oracle_hash','source_rulings_hash')):
                shadowed.add(old.key)
                matched_sources.add((old.provenance['source_oracle_hash'],old.provenance['source_rulings_hash']))
    # A source-identical empty compatibility cast has no extra result beyond
    # the existing normal permanent card-form owner. Keep all nonempty views.
    if matched_sources and record.is_permanent_spell and all(
            p.trust_level=='trusted' and not p.requires_arbiter for p in generated):
        for old in reviewed:
            if (old.oracle_id==record.oracle_id and old.trust_level=='trusted'
                    and not str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    and old.ability_id=='spell:front' and old.active_zone=='stack'
                    and old.event=='resolve' and old.destination=='battlefield'
                    and not old.requires_arbiter and not old.effects and not old.handlers
                    and old.target_schema is None and old.cost_schema is None
                    and old.event_condition is None and old.trigger_limit is None
                    and (old.provenance.get('source_oracle_hash'),old.provenance.get('source_rulings_hash')) in matched_sources):
                shadowed.add(old.key)
    return shadowed
