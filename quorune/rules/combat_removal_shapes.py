"""Close one represented combat-removal instruction or blocked-attacker choice."""

from collections.abc import Mapping

from ..combat_removal import (
    BLOCKED_ATTACKER_REFERENCE, COMBAT_REMOVAL_CAPABILITY, COMBAT_REMOVAL_MECHANIC,
    COMBAT_REMOVAL_OPERATION, combat_removal_instruction,
)
from .effect_shape_walkers import nested_effect_operations
from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

_TARGET = {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}
_BLOCKED_SCOPE = (
    {'field': 'card', 'op': 'eq', 'value': '$source.ref'},
    {'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
)
_OPTIONAL_CAPABILITY = 'effect.choice.optional_fixed'
_UNTAP_CAPABILITY = 'permanent.untap.effect'
_BLOCK_EVENT_CAPABILITY = 'trigger.event.normalized_public_action'


def _combat_removal_capabilities(effects, target_schema):
    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    try:
        reference, untap = combat_removal_instruction(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    if reference == BLOCKED_ATTACKER_REFERENCE and untap == 'before' and target_schema is None:
        return (COMBAT_REMOVAL_CAPABILITY, _UNTAP_CAPABILITY, _BLOCK_EVENT_CAPABILITY)
    if reference != '$target.0' or untap == 'before' or not isinstance(target_schema, Mapping):
        return ()
    expected = ({**_TARGET, 'controller_relation': 'you', 'combat_state': 'attacking'} if untap == 'after'
                else {**_TARGET, 'combat_state': 'attacking_or_blocking'})
    if target_schema != expected or type(target_schema.get('count')) is not int:
        return ()
    return (COMBAT_REMOVAL_CAPABILITY, 'target.revalidate_resolution', *direct_target_predicate_capabilities(target_schema),
            *((_UNTAP_CAPABILITY,) if untap == 'after' else ()))


def combat_removal_node_capabilities(*, effects, target_schema, mechanic_ids):
    if COMBAT_REMOVAL_MECHANIC not in mechanic_ids:
        return ()
    return _combat_removal_capabilities(effects, target_schema)


def combat_removal_runtime_dependencies(program):
    effects = program.effects
    if COMBAT_REMOVAL_OPERATION not in nested_effect_operations(effects):
        return ()
    if len(effects) == 1 and effects[0].get('op') == 'offer_optional_effect':
        wrapper = effects[0]
        if (set(wrapper) != {'op', 'player', 'effects'} or wrapper['player'] != '$controller'
                or program.event != 'creature.becomes_blocked' or program.active_zone != 'battlefield'
                or program.event_condition not in _BLOCKED_SCOPE or program.target_schema is not None
                or not isinstance(wrapper['effects'], (list, tuple))):
            raise ValueError('Optional removal requires one original newly blocked attacker')
        required = _combat_removal_capabilities(wrapper['effects'], None)
        if required:
            return (*required, _OPTIONAL_CAPABILITY)
    elif program.target_schema is not None:
        required = _combat_removal_capabilities(effects, program.target_schema)
        if required:
            return required
    raise ValueError('Combat removal requires one closed targeted or becoming-blocked result')


def is_closed_combat_removal_program(program):
    try:
        required = combat_removal_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
