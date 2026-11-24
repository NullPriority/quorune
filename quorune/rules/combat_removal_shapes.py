"""Close one represented combat-removal instruction or blocked-attacker choice."""

from collections.abc import Mapping

from ..combat_removal import (
    BLOCKED_ATTACKER_REFERENCE, COMBAT_REMOVAL_CAPABILITY, COMBAT_REMOVAL_MECHANIC,
    COMBAT_REMOVAL_OPERATION, combat_removal_instruction,
)
from ..delayed_object_model import is_forced_block_status_effect
from .effect_shape_walkers import nested_effect_operations, nested_effects
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
    if reference != '$target.0' or not isinstance(target_schema, Mapping):
        return ()
    if untap is None:
        expected = {**_TARGET, 'combat_state': 'attacking_or_blocking'}
    else:
        expected = {**_TARGET, 'combat_state': 'attacking',
                    'controller_relation': 'opponent' if untap == 'before' else 'you'}
    if target_schema != expected or type(target_schema.get('count')) is not int:
        return ()
    return (COMBAT_REMOVAL_CAPABILITY, 'target.revalidate_resolution', *direct_target_predicate_capabilities(target_schema),
            *((_UNTAP_CAPABILITY,) if untap is not None else ()))


def _forced_block_status_capabilities(effect, target_schema):
    from ..delayed_object_model import (
        FORCED_BLOCK_STATUS_CAPABILITIES, forced_block_status_target_schema, validate_forced_block_status_effect,
    )
    from ..replacement.immutable import thaw_value

    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != forced_block_status_target_schema()):
        return ()
    try:
        validate_forced_block_status_effect(effect, compiled=True)
    except (TypeError, ValueError):
        return ()
    return FORCED_BLOCK_STATUS_CAPABILITIES


def _forced_block_status_runtime_dependencies(program, effect):
    from ..compiler.delayed_draw_templates import fixed_next_turn_upkeep_draw_effect
    from ..replacement.immutable import thaw_value

    required = _forced_block_status_capabilities(effect, program.target_schema)
    if not required or not program.effects or effect != program.effects[0]:
        raise ValueError("Blocked status requires its exact first attacking-creature result")
    event_zone = (program.event, program.active_zone)
    if event_zone == ("activate", "battlefield") and len(program.effects) == 1:
        return required
    if event_zone == ("resolve", "stack") and len(program.effects) == 2:
        tail = thaw_value(program.effects[1])
        if tail in ({"op": "draw", "player": "$controller", "count": 1, "private": True},
                    fixed_next_turn_upkeep_draw_effect()):
            return required
    raise ValueError("Blocked status requires its complete activation or spell with the fixed draw")


def combat_removal_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) == 1 and is_forced_block_status_effect(effects[0]) and 'cr-115-targets' in mechanic_ids:
        return _forced_block_status_capabilities(effects[0], target_schema)
    if COMBAT_REMOVAL_MECHANIC not in mechanic_ids:
        return ()
    return _combat_removal_capabilities(effects, target_schema)


def combat_removal_runtime_dependencies(program):
    effects = program.effects
    forced = tuple(effect for effect in nested_effects(effects) if is_forced_block_status_effect(effect))
    if forced:
        if len(forced) != 1:
            raise ValueError('Blocked status requires one closed attacker result')
        return _forced_block_status_runtime_dependencies(program, forced[0])
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
    if len(program.effects) == 1 and is_forced_block_status_effect(program.effects[0]):
        required = _forced_block_status_capabilities(program.effects[0], program.target_schema)
        return bool(required) and set(required).issubset(program.capability_dependencies)
    try:
        required = combat_removal_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
