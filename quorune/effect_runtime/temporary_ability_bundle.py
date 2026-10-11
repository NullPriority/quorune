from __future__ import annotations

"""Temporary printed or explicit ability grants through the continuous owner."""

from ..continuous_effect_model import ContinuousOperation,Layer
from ..continuous_effect_state import create_resolution_continuous_effect,resolution_effect_source
from ..errors import GameRuleError
from ..rules.temporary_ability_grant import decode_temporary_ability_grant,BACKUP_CONTEXT,BACKUP_COVERAGE


def printed_backup_source_context(source,program):
    if BACKUP_COVERAGE not in program.coverage:return {}
    # Copied Backup retains its original printed ordering (CR 702.165b).
    # The compiler-pinned bundle excludes abilities subsequently gained.
    return {BACKUP_CONTEXT:True}


def apply_temporary_ability_bundle(host,effect,*,actor,reason):
    instruction={k:v for k,v in effect.items() if k!='_runtime_source'}
    stale=instruction.get('card') is None
    if stale:instruction['card']='stale-resolved-reference'
    try:
        keywords,fragments=decode_temporary_ability_grant(instruction)
        if stale:return []
        card=host._resolve_object(actor,instruction['card'],zones={'battlefield'})
        source=resolution_effect_source(host,effect)
        if instruction['exclude_source'] and (source.object_id is None or source.logical_object_id is None):raise GameRuleError('Source-excluding ability grant requires a pinned source incarnation')
        if instruction['exclude_source'] and (source.object_id,source.logical_object_id)==(card.object_id,card.logical_object_id):return []
        if instruction['printed_only']:
            item=next((i for i in host.state.stack if i.ref==source.stack_ref),None)
            if item is None or type(item.context.get(BACKUP_CONTEXT)) is not bool:raise GameRuleError('Printed ability grant requires its locked source origin')
            if not item.context[BACKUP_CONTEXT]:return []
        for fragment in fragments:
            if fragment['kind'] in {'granted_activated','granted_triggered'}:
                program=host.semantics.get(fragment['value']['semantic_key'])
                if program is None or not host.semantic_program_is_current_trusted(program):raise GameRuleError('Temporary donated ability requires a trusted child program')
        operations=tuple([*(ContinuousOperation('add_ability',k) for k in keywords),*(ContinuousOperation('add_ability_fragment',value) for value in fragments)])
        if operations:create_resolution_continuous_effect(host,source=source,targets=(card,),layer=Layer.ABILITY,sublayer='6',operations=operations)
    except (TypeError,ValueError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    host._log(actor,'permanent.abilities','Temporary ability bundle resolved.',{'object':card.ref,'reason':reason},importance=1,changed_objects=[card.object_id])
    return [card.ref]
