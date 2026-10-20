from __future__ import annotations

"""Validate already-bound conditional result children through their owners."""

from collections.abc import Mapping

from .model import SemanticChoiceError


def _self_replacement_apnap(child, actor, mechanics):
    from ..rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
    from ..rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
    from ..rules.fractional_player_result_shapes import half_choice_node_capabilities

    players = child.get('players')
    if child.get('actor') != actor or players not in ('all', 'opponents', [actor]) or 'target' in child:
        raise SemanticChoiceError('Conditional APNAP result has an unbound actor/domain')
    symbolic = {**dict(child), 'actor': '$controller', 'players': ['$controller'] if players == [actor] else players}
    if isinstance(child.get('count'), Mapping):
        capabilities = half_choice_node_capabilities(effects=(symbolic,), target_schema=None, mechanic_ids=mechanics)
    elif child.get('then') == 'sacrifice':
        capabilities = fixed_affected_player_sacrifice_node_capabilities(effects=(symbolic,), target_schema=None, mechanic_ids=mechanics)
    else:
        capabilities = fixed_affected_player_discard_node_capabilities(effects=(symbolic,), target_schema=None,
            mechanic_ids=mechanics, allow_controller=True)
    if not capabilities:
        raise SemanticChoiceError('Conditional APNAP result lacks its closed result owner')


def validate_resolution_result_children(effect, effects, otherwise, context):
    from .optional_effect import _represented_effect

    for children, mechanics in ((effects, effect['mechanic_ids']), (otherwise, effect.get('otherwise_mechanic_ids', ()))):
        for child in children:
            if child.get('op') == 'choose_cards_apnap' and effect.get('schema_version') in {3,4}:
                _self_replacement_apnap(child, context.actor, mechanics)
            elif child.get('op') == 'choose_cards_apnap' and effect.get('schema_version') == 2:
                from ..rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
                symbolic = {**dict(child), 'actor': '$controller'}
                if child.get('actor') != context.actor or child.get('players') != 'opponents' or not fixed_affected_player_discard_node_capabilities(
                    effects=(symbolic,), target_schema=None, mechanic_ids=effect['mechanic_ids'],
                ):
                    raise SemanticChoiceError('Cast condition APNAP discard is malformed')
            else:
                _represented_effect(child, actor=context.actor, query=context.query)
