from __future__ import annotations

"""Resolution and target binding for combat-bound block requirements."""

from collections.abc import Mapping

from ..blocking_requirement_model import ResolvedBlockingRequirement,BLOCKING_REQUIREMENT_OPERATION
from ..continuous_effect_model import ContinuousObjectIdentity
from ..combat_relationship_state import commit_blocking_requirement
from ..tap_state import untap_permanent


PROVOKE_CAPABILITY='trigger.keyword.provoke.fixed'


def bound_defending_controller_schema(schema,context):
    if not isinstance(schema,Mapping) or schema.get('controller_seat')!='$context.defending_player':return schema
    player=context.get('defending_player')
    if type(player) is not str or not player or player.startswith('$'):
        raise ValueError('Defending-player target binding is unavailable')
    return {**schema,'controller_seat':player}


def resolve_blocking_requirement(host,effect,*,actor,reason):
    allowed={'op','attacker','blocker','untap_blocker','reason','_runtime_source'}
    if (set(effect)-allowed or effect.get('op')!=BLOCKING_REQUIREMENT_OPERATION
            or type(effect.get('untap_blocker')) is not bool):
        raise ValueError('Combat blocking requirement instruction is malformed')
    if effect.get('blocker') is None:return None
    blocker=host._resolve_object(actor,str(effect['blocker']),zones={'battlefield'})
    attacker_value=effect.get('attacker')
    attacker=host._resolve_object(actor,str(attacker_value),zones={'battlefield'}) if attacker_value is not None else None
    if attacker is not None and attacker.object_id in host.state.combat.attackers:
        requirement=ResolvedBlockingRequirement(host._next_ref('BR'),
            ContinuousObjectIdentity(attacker.object_id,attacker.logical_object_id),
            ContinuousObjectIdentity(blocker.object_id,blocker.logical_object_id))
        commit_blocking_requirement(host.state.combat,requirement=requirement)
    if effect['untap_blocker']:untap_permanent(host,blocker,actor=actor,reason=reason)
    return blocker.ref
