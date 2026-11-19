"""Read current public giver rules without changing keywords or tapped state."""
from ..ability_fragments import canonical_ability_fragments
from ..attachments import attached_object_identity
from ..block_as_though_model import BlockAsThoughAssumptions,BlockAsThoughPermission
from ..characteristic_evaluation import type_parts


def current_block_as_though_assumptions(host,blocker,attacker=None):
    active=set()
    for source in sorted(host.state.cards.values(),key=lambda card:card.ref):
        if source.zone!='battlefield' or source.phased_out:
            continue
        for permission in canonical_ability_fragments(host._effective_ability_fragments(source)):
            if not isinstance(permission,BlockAsThoughPermission):continue
            if permission.scope=='self':
                if (source.object_id,source.logical_object_id)!=(blocker.object_id,blocker.logical_object_id):continue
            elif permission.scope=='attached_blocker':
                identity=attached_object_identity(host.state.cards,source)
                if identity is None or (identity.object_id,identity.logical_object_id)!=(blocker.object_id,blocker.logical_object_id):continue
            elif source.controller!=blocker.controller or not blocker.tapped:
                continue
            if permission.assumption=='reach_against_dragons':
                if attacker is None or 'dragon' not in type_parts(str(host._effective_card_data(attacker).get('type_line') or ''))[1]:continue
            active.add(permission.assumption)
    return BlockAsThoughAssumptions(**{field:field in active for field in BlockAsThoughAssumptions.__dataclass_fields__})


def blocker_tap_allows_block(host,blocker):
    return not blocker.tapped or current_block_as_though_assumptions(host,blocker).untapped
